"""Coverage feedback respects scope, failures, accounting and persisted limits."""
from dataclasses import replace

import pytest

from paper_assistant.ai.types import DecisionError
from paper_assistant.research.context import _compact
from paper_assistant.research.state import ModelEstimate
from tests.research.test_agent_runner import action, normal_policy, resumed, runner
from tests.research.test_agent_tools import tools
from tests.tools.test_paper_fetcher import FakeResponse, candidates, task


def prepared(tmp_path, sample_pdf, *, config=None):
    runtime = tools(tmp_path, sample_pdf, seed=candidates()[:2],
                    fetch_responses=[FakeResponse(sample_pdf.read_bytes()), FakeResponse(sample_pdf.read_bytes())],
                    research_task=replace(task(), limits=replace(task().limits, max_model_cost_usd=.1)))
    agent = runner(tmp_path, normal_policy, runtime=runtime, config=config)
    agent.run(max_steps_per_run=3)
    assert agent.state.evidence
    other = next(r for r in agent.state.candidates if r['arxiv_id'] != '2401.00396')
    agent.state.selections[other['arxiv_id']] = {'arxiv_id': other['arxiv_id'], 'selected': True, 'reason': 'Relevant comparison'}
    return agent, other


def test_selected_gap_survives_minimal_model_context_and_resume(tmp_path, sample_pdf):
    agent, other = prepared(tmp_path, sample_pdf)
    compact = _compact(agent.view(), minimal=True, history=0, evidence_count=1)
    gaps = compact['completion']['selected_paper_coverage']
    assert next(r for r in gaps if r['arxiv_id'] == other['arxiv_id'])['next_tool'] == 'fetch_paper'
    before = dict(agent.state.usage)
    item = agent._selected_review(ModelEstimate())
    agent._request_selected_review(item)
    restored = resumed(agent)
    assert restored.state.last_error['suggested_arguments'] == {'arxiv_id': other['arxiv_id']}
    assert restored._selected_review(ModelEstimate()) is None
    assert restored.state.usage == before  # No refund, reset or unrecorded request.


@pytest.mark.parametrize('boundary', ['money', 'tokens', 'steps', 'tool-budget', 'fetch-budget', 'failed', 'unselected', 'hard-answer'])
def test_partial_delivery_does_not_force_acquisition_past_limits(tmp_path, sample_pdf, boundary):
    agent, other = prepared(tmp_path, sample_pdf)
    if boundary == 'money':
        agent.state.usage['cost_usd'] = .079
    elif boundary == 'tokens':
        agent.state.usage['tokens'] = agent.config.max_model_tokens - agent.config.report_reserve_tokens - 1000
    elif boundary == 'steps':
        agent.state.steps = agent.config.max_steps - 3
    elif boundary == 'tool-budget':
        agent.tools._calls = agent.tools.task.limits.max_tool_calls - 3
    elif boundary == 'fetch-budget':
        agent.tools.fetch.restore_attempts(agent.tools.task.limits.max_fetch_attempts)
    elif boundary == 'failed':
        # A known failed ingest must not become an implicit retry instruction.
        other['status'] = 'downloaded'
        agent.state.observations.append({'step': 4, 'tool': 'ingest_paper',
            'arguments': {'arxiv_id': other['arxiv_id']}, 'result': {'success': False}})
    elif boundary == 'unselected':
        agent.state.selections[other['arxiv_id']]['selected'] = False
    else:
        agent._require_answer('model_budget')
    assert agent._selected_review(ModelEstimate(max_cost_usd=.01)) is None


def test_scoped_empty_search_is_an_honest_gap_not_a_reason_to_loop(tmp_path, sample_pdf):
    agent, other = prepared(tmp_path, sample_pdf)
    # Real source acquisition, indexing and empty retrieval through the host.
    plan = [action('fetch_paper', arxiv_id=other['arxiv_id']),
            action('ingest_paper', arxiv_id=other['arxiv_id']),
            action('search_documents', document_ids=[other['document_id']], query='zzzznonexistent', method='bm25')]
    agent.policy.choose = lambda view: plan.pop(0)
    agent.run(max_steps_per_run=3)
    assert agent.state.observations[-1]['result']['success']
    assert not agent.state.observations[-1]['result']['data']['results']
    assert agent._selected_review(ModelEstimate()) is None


def test_gathering_context_failure_preserves_restricted_answer_exit(tmp_path, sample_pdf):
    agent, _ = prepared(tmp_path, sample_pdf)
    agent._require_answer('no_progress')
    before = dict(agent.state.usage)

    def estimate(view):
        if not view['completion'].get('answer_required'):
            raise DecisionError('context_too_large', 'Full context does not fit')
        return ModelEstimate()

    agent.policy.estimate = estimate
    prepared_turn = agent.prepare_request()
    assert prepared_turn.context['completion']['answer_required']
    assert agent.state.answer_phase == 'no_progress'
    assert agent.state.usage == before
