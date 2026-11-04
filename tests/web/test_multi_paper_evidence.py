"""Controlled source/model responses through the existing Web research boundary.

Synthetic PDFs prove provenance and publication behavior, not model quality.
"""
import io
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from paper_assistant.research.agent import MockPolicy, ResearchAgent
from paper_assistant.research.toolset import ResearchTools
from paper_assistant.research.workspace import ResearchWorkspace
from paper_assistant.tools.arxiv.fetch import PaperFetcher
from paper_assistant.tools.arxiv.search import LiteratureSearchSession
from paper_assistant.web.jobs import ResearchJobService
from tests.research.test_agent_tools import LIVE
from tests.research.test_research_workspace import WorkspaceEmbedder, local_parser
from tests.tools.test_literature_search import FakeTransport as SearchTransport, client as search_client
from tests.tools.test_paper_fetcher import FakeResponse, FakeTransport, NoWaitGate, task
from paper_assistant.tools.arxiv.search import HTTPResponse
from tests.web.test_research_web import payload, wait_finished
from tests.web.test_web_app import send_request


def synthetic_pdf(text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
        NameObject('/F1'): DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 50 700 Td ({text}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


@pytest.mark.parametrize('count,missing', [(2, False), (3, False), (3, True)])
def test_web_research_preserves_pinned_multi_paper_evidence_and_actual_material_scope(tmp_path, monkeypatch, count, missing):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-fixture-no-network')
    texts = ['Method Alpha evaluates dataset A with model Small; score 80.',
             'Method Beta evaluates dataset B with model Large; score 90.',
             'Method Gamma evaluates dataset C with model Medium; score 85.']
    request_task = replace(task(), target_papers=count)
    attempts = []
    failures = []

    def execute(command, directory):
        fetcher = PaperFetcher(cache_directory=tmp_path / 'pdf-cache', gate=NoWaitGate(), transport=FakeTransport([
            FakeResponse(status=404) if missing and i == count - 1 else FakeResponse(synthetic_pdf(texts[i])) for i in range(count)]))
        workspace = ResearchWorkspace(directory.name, request_task, (), root=directory.parent, fetcher=fetcher,
            embedder_factory=WorkspaceEmbedder, parser=local_parser)
        source = search_client(tmp_path, SearchTransport([HTTPResponse(200, 'application/atom+xml', LIVE.read_bytes())]))
        runtime = ResearchTools(workspace, search_session=LiteratureSearchSession(request_task, source))
        plan = []
        def choose(view):
            if not view['candidates']:
                return {'kind': 'tool', 'tool': 'search_literature', 'arguments': {'query': 'retrieval augmented generation'}}
            if not plan and not attempts:
                for i, row in enumerate(view['candidates'][:count]):
                    plan.append(('fetch_paper', {'arxiv_id': row['arxiv_id']}))
                    if not (missing and i == count - 1):
                        plan.append(('ingest_paper', {'arxiv_id': row['arxiv_id']}))
                        plan.append(('read_pages', {'document_id': row['document_id'], 'start_page': 1}))
            if plan:
                tool, arguments = plan.pop(0)
                attempts.append(tool)
                return {'kind': 'tool', 'tool': tool, 'arguments': arguments}
            evidence = list(view['evidence'].items())
            citations = [{'evidence_id': identifier, 'quote': row['text']} for identifier, row in evidence]
            if not failures:
                # A quote from paper B attributed to A must not pass even though both papers are in scope.
                citations[0]['quote'] = evidence[1][1]['text']
                failures.append(True)
            elif any(row['tool'] == 'verify_citations' and row['result']['success'] for row in view['observations']):
                return {'kind': 'finish', 'reason': 'insufficient_evidence' if missing else 'sufficient_evidence', 'summary': '已按现有正文回答'}
            answer = f'材料范围：实际取得 {len(evidence)} 篇正文。\n' + '\n'.join(
                f"方法与实验条件：论文 {row['source']['arxiv_id']} 的原文为 {row['text']} [{identifier}]" for identifier, row in evidence)
            answer += '\n方法差异：数据集与模型条件不同，指标不可直接比较。局限与缺口：当前摘录不足以判断统一设置下的优劣。'
            if missing:
                answer += '\n第三篇全文获取失败（404），未将候选摘要作为正文。'
            return {'kind': 'tool', 'tool': 'verify_citations', 'arguments': {
                'answer': answer, 'insufficient_evidence': missing, 'citations': citations}}
        ResearchAgent(runtime, MockPolicy(choose)).run()
        return 0

    service = ResearchJobService(tmp_path, threading.Lock(), execute=execute)
    server = SimpleNamespace(research=service, server_address=('127.0.0.1', 8501), question_lock=service.work_lock)
    try:
        code, _, body = send_request(server, '/api/research/jobs', payload=payload(target_papers=count), origin='http://127.0.0.1:8501')
        assert code == 202
        identifier = json.loads(body)['job']['task_id']
        wait_finished(service, identifier)
        code, _, body = send_request(server, f'/api/research/jobs/{identifier}', method='GET')
        result = json.loads(body)
        assert code == 200 and result['job']['status'] == 'finished', result
        checked = result['state']['evidence_answer']
        assert checked is not None, result
        expected_count = count - int(missing)
        assert len(checked['citations']) == expected_count
        assert len({row['source']['document_id'] for row in checked['citations']}) == expected_count
        for i, citation in enumerate(checked['citations']):
            assert citation['quote'] == texts[i]
            assert citation['source']['version'] > 0
            assert citation['sources'][0]['page_number'] == 1
            assert citation['sources'][0]['document_id'] == citation['source']['document_id']
        assert checked['traceability'] == 'passed'
        assert checked['claim_support'] == 'not_assessed'
        assert checked['insufficient_evidence'] == missing
        assert '不可直接比较' in checked['answer']
        assert f'实际取得 {expected_count} 篇正文' in checked['answer']
        checks = [event for event in result['events'] if event['tool'] == 'verify_citations']
        assert any(not event['success'] for event in checks)
        assert checks[-1]['success']
        if missing:
            assert result['state']['stop_reason'] == 'insufficient_evidence'
            assert sum(row['status'] == 'fulltext_ready' for row in result['state']['candidates']) == 2
            assert '404' in checked['answer']
    finally:
        service.close()
