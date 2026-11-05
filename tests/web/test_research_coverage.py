"""Replay premature multi-paper completion at the existing Web boundary.

Only the model/source responses are controlled. Host, tools, indexing, evidence,
budget accounting and HTTP publication are real; this is not model-quality proof.
"""
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from paper_assistant.research.agent import MockPolicy, ResearchAgent
from paper_assistant.research.state import RunnerConfig
from paper_assistant.research.toolset import ResearchTools
from paper_assistant.research.workspace import ResearchWorkspace
from paper_assistant.tools.arxiv.fetch import PaperFetcher
from paper_assistant.web.jobs import ResearchJobService
from tests.research.test_research_workspace import WorkspaceEmbedder, local_parser
from tests.tools.test_paper_fetcher import FakeResponse, FakeTransport, NoWaitGate, candidates, task
from tests.web.test_multi_paper_evidence import synthetic_pdf
from tests.web.test_research_web import payload, wait_finished
from tests.web.test_web_app import send_request


@pytest.mark.parametrize('second_indexed,ending,search_scope', [
    pytest.param(indexed, ending, 'none', id=f"{'indexed-not-read' if indexed else 'selected-not-acquired'}-{ending}")
    for indexed in (False, True) for ending in ('finish', 'verify', 'no-progress')
] + [
    pytest.param(True, 'finish', 'empty', id='empty-scope-still-needs-review'),
    pytest.param(True, 'finish', 'omitted', id='unscoped-empty-result-is-honest-gap'),
])
def test_web_cannot_publish_partial_answer_before_reviewing_selected_paper(tmp_path, monkeypatch, second_indexed, ending, search_scope):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-offline-coverage-fixture')
    papers = candidates()[:2]
    question = 'Compare Alpha and Beta training methods.'
    request_task = replace(task(), target_papers=2, research_questions=(question,),
                           limits=replace(task().limits, max_model_cost_usd=.1))
    offered_partial = False
    offered_finish = False
    repeated_reads = 0
    searched = False
    search_coverage = None
    search_observation = None
    review_seen = False

    def execute(command, directory):
        fetcher = PaperFetcher(cache_directory=tmp_path / 'pdf-cache', gate=NoWaitGate(),
            transport=FakeTransport([FakeResponse(synthetic_pdf('Alpha uses supervised training.')),
                                     FakeResponse(synthetic_pdf('Beta uses contrastive training.'))]))
        workspace = ResearchWorkspace(directory.name, request_task, papers, root=directory.parent,
            fetcher=fetcher, embedder_factory=WorkspaceEmbedder, parser=local_parser)
        runtime = ResearchTools(workspace)

        def choose(view):
            nonlocal offered_partial, offered_finish, repeated_reads, searched, search_coverage, search_observation, review_seen
            if (view.get('last_error') or {}).get('code') == 'selected_paper_needs_review':
                offered_partial = offered_finish = True
                review_seen = True
            rows = {row['arxiv_id']: row for row in view['candidates']}
            first, second = (rows[p.arxiv_id] for p in papers)
            observed = {row['source']['document_id'] for row in view['evidence'].values()}
            if offered_partial and not offered_finish:
                offered_finish = True
                return {'kind': 'finish', 'reason': 'insufficient_evidence',
                        'summary': 'Only the first paper has visible evidence.'}
            if not first['source_sha256']:
                return {'kind': 'tool', 'tool': 'fetch_paper', 'arguments': {'arxiv_id': first['arxiv_id']},
                    'updates': {'selections': [{'arxiv_id': p.arxiv_id, 'selected': True,
                        'reason': 'Relevant method requested in the comparison.'} for p in papers]}}
            if first['status'] != 'fulltext_ready':
                return {'kind': 'tool', 'tool': 'ingest_paper', 'arguments': {'arxiv_id': first['arxiv_id']}}
            if first['document_id'] not in observed:
                return {'kind': 'tool', 'tool': 'read_pages', 'arguments': {'document_id': first['document_id'], 'start_page': 1}}
            if second_indexed or offered_partial:
                if not second['source_sha256']:
                    return {'kind': 'tool', 'tool': 'fetch_paper', 'arguments': {'arxiv_id': second['arxiv_id']}}
                if second['status'] != 'fulltext_ready':
                    return {'kind': 'tool', 'tool': 'ingest_paper', 'arguments': {'arxiv_id': second['arxiv_id']}}
            if not offered_partial and search_scope != 'none':
                if not searched:
                    searched = True
                    arguments = {'query': 'zzzznonexistent', 'method': 'bm25'}
                    if search_scope == 'empty':
                        arguments['document_ids'] = []
                    return {'kind': 'tool', 'tool': 'search_documents', 'arguments': arguments}
                search_observation = view['observations'][-1]
                search_coverage = next(row for row in view['completion']['selected_paper_coverage']
                                       if row['arxiv_id'] == second['arxiv_id'])
            if not offered_partial:
                if ending == 'no-progress' and repeated_reads < 2:
                    repeated_reads += 1
                    return {'kind': 'tool', 'tool': 'read_pages', 'arguments': {'document_id': first['document_id'], 'start_page': 1}}
                offered_partial = True
                evidence_id, record = next(iter(view['evidence'].items()))
                return {'kind': 'tool', 'tool': 'verify_citations', 'arguments': {
                    'answer': f'Only Alpha has been read; Beta evidence is missing. [{evidence_id}]',
                    'insufficient_evidence': ending != 'verify',
                    'citations': [{'evidence_id': evidence_id, 'quote': record['text']}]}}
            if second['document_id'] not in observed:
                return {'kind': 'tool', 'tool': 'read_pages', 'arguments': {'document_id': second['document_id'], 'start_page': 1}}
            return {'kind': 'tool', 'tool': 'verify_citations', 'arguments': {
                'answer': '\n'.join(f"{row['text']} [{identifier}]" for identifier, row in view['evidence'].items()),
                'insufficient_evidence': False,
                'citations': [{'evidence_id': identifier, 'quote': row['text']} for identifier, row in view['evidence'].items()]}}

        ResearchAgent(runtime, MockPolicy(choose), config=RunnerConfig(max_steps=12, max_model_tokens=60000)).run()
        return 0

    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    server = SimpleNamespace(research=service, server_address=('127.0.0.1', 8501))
    try:
        status, _, body = send_request(server, payload=payload(target_papers=2, questions=[question]))
        assert status == 202
        identifier = json.loads(body)['job']['task_id']
        wait_finished(service, identifier)
        status, _, body = send_request(server, f'/api/research/jobs/{identifier}', method='GET')
        snapshot = json.loads(body)
        assert status == 200 and snapshot['job']['status'] == 'finished'
        if search_scope != 'none':
            assert search_coverage is not None
            assert search_coverage['read_attempted'] is (search_scope == 'omitted')
            assert search_coverage['body_evidence_observed'] is False
            assert ('next_tool' in search_coverage) is (search_scope == 'empty')
            assert review_seen is (search_scope == 'empty')
            assert search_observation['tool'] == 'search_documents'
            assert search_observation['result']['success']
            assert search_observation['result']['data']['results'] == []
        expected_stop = 'insufficient_evidence' if search_scope == 'omitted' else 'sufficient_evidence'
        assert snapshot['state']['stop_reason'] == expected_stop, snapshot['state']['stop_reason']
        checked = snapshot['state']['evidence_answer']
        assert len({c['source']['document_id'] for c in checked['citations']}) == (1 if search_scope == 'omitted' else 2)
        expected_quotes = ['Alpha uses supervised training.']
        if search_scope != 'omitted':
            expected_quotes.append('Beta uses contrastive training.')
        assert [citation['quote'] for citation in checked['citations']] == expected_quotes
        assert checked['traceability'] == 'passed'
        assert snapshot['state']['usage']['cost_usd'] <= .1
        assert snapshot['state']['steps'] <= 12
    finally:
        service.close()
