import json
from pathlib import Path

from paper_assistant.material_preparation import prepare_materials
from paper_assistant.tools.arxiv.search import HTTPResponse


class UnavailableMetadata:
    def get(self, url, *, timeout, max_bytes):
        return HTTPResponse(503, 'application/atom+xml', b'unavailable')


def test_unavailable_official_metadata_leaves_explicit_blocker_and_no_seed(tmp_path):
    root = Path(__file__).parents[2]
    out = tmp_path / 'attempt'
    report = prepare_materials(root, out, metadata_transport=UnavailableMetadata())
    assert report['status'] == 'blocked'
    assert report['blockers'] == ['metadata:http_503']
    assert not (out / 'seed.json').exists()
    assert json.loads((out / 'report.json').read_text()) == report


from tests.tools.test_paper_fetcher import FakeResponse, FakeTransport, NoWaitGate
from paper_assistant.tools.arxiv.fetch import load_search_candidates
import pytest


class CapturedMetadata:
    def __init__(self, body):
        self.body = body
        self.urls = []

    def get(self, url, *, timeout, max_bytes):
        self.urls.append(url)
        return HTTPResponse(200, 'application/atom+xml', self.body)


@pytest.fixture
def material_project(tmp_path):
    fixture = Path(__file__).parents[1] / 'fixtures/arxiv'
    metadata = json.loads((fixture / 'live_20261023.meta.json').read_text())
    root = tmp_path / 'project'
    directory = root / 'eval_data/agent_v1'
    directory.mkdir(parents=True)
    sources = {r['arxiv_id']: {**r, 'evidence': []} for r in metadata['expected_candidates']}
    (directory / 'sources.json').write_text(json.dumps({
        'sources': sources, 'fixed_seed': {'path': 'data/literature_search/smoke/20261023.json'}}))
    (directory / 'suite.json').write_text(json.dumps({'cases': [{'web_payload': {
        'start_date': '2023-01-01', 'end_date': '2024-01-01'}}]}))
    return root, (fixture / 'live_20261023.xml').read_bytes()


def test_material_drafts_cannot_be_mistaken_for_live_or_reviewed_evidence(material_project, sample_pdf):
    root, body = material_project
    metadata = CapturedMetadata(body)
    out = root / 'data/materials/attempt'
    report = prepare_materials(root, out, metadata_transport=metadata,
                              pdf_transport=FakeTransport([FakeResponse(sample_pdf.read_bytes()) for _ in range(3)]),
                              gate=NoWaitGate())
    assert report['status'] == 'blocked'
    assert report['model_calls'] == 0
    assert 'metadata_not_live' in report['blockers']
    with pytest.raises(ValueError, match='Invalid search report'):
        load_search_candidates(out / 'seed.json')
    _, candidates = load_search_candidates(out / 'seed.json', allow_injected=True)
    assert {r.version for r in candidates} == {2, 3}
    assert 'id_list=' in metadata.urls[0] and '2401.00396v2' in metadata.urls[0]
    drafts = json.loads((out / 'evidence.draft.json').read_text())
    assert all(d['page_number'] >= 1 and d['quote'] and not d['claim'] and not d['reviewer']
               for entries in drafts.values() for d in entries)
    prepared = json.loads((out / 'sources.prepared.json').read_text())
    assert all(s['page_count'] > 0 and s['pdf_sha256'] and s['evidence'] == []
               for s in prepared['sources'].values())
    assert not (root / prepared['fixed_seed']['path']).exists()


def test_changed_version_is_not_silently_revised(material_project):
    root, body = material_project
    path = root / 'eval_data/agent_v1/sources.json'
    book = json.loads(path.read_text())
    book['sources']['2401.00396']['version'] = 1
    path.write_text(json.dumps(book))
    out = root / 'data/materials/mismatch'
    report = prepare_materials(root, out, metadata_transport=CapturedMetadata(body), gate=NoWaitGate())
    assert report['blockers'] == ['metadata:pinned_versions_mismatch']
    assert not (out / 'seed.json').exists()
    assert not (root / 'data/paper_cache').exists()


def test_pdf_failure_does_not_count_as_ready_material(material_project):
    root, body = material_project
    out = root / 'data/materials/pdf-failure'
    report = prepare_materials(root, out, metadata_transport=CapturedMetadata(body),
                              pdf_transport=FakeTransport([FakeResponse(status=404) for _ in range(3)]),
                              gate=NoWaitGate())
    assert report['status'] == 'blocked'
    assert '2401.00396:unavailable' in report['blockers']
    book = json.loads((out / 'sources.prepared.json').read_text())
    assert all(s['pdf_path'] is None for s in book['sources'].values())
    with pytest.raises(FileExistsError):
        prepare_materials(root, out, metadata_transport=UnavailableMetadata())


def test_default_http_adapters_produce_compatible_seed_but_require_human_review(material_project, sample_pdf, monkeypatch):
    # HTTP boundary responses are controlled; this proves adapter behavior only,
    # and these temporary artifacts are never published as real captured results.
    from types import SimpleNamespace
    from tests.tools.test_paper_fetcher import MockHTTPStream
    import paper_assistant.tools.arxiv.search as search
    import paper_assistant.tools.arxiv.fetch as fetch
    root, body = material_project

    def metadata_open(request, *, timeout):
        result = MockHTTPStream(body)
        result.status = 200
        result.headers['Content-Type'] = 'application/atom+xml'
        return result

    monkeypatch.setattr(search, 'build_opener', lambda *a: SimpleNamespace(open=metadata_open))
    monkeypatch.setattr(fetch, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda request, timeout: MockHTTPStream(sample_pdf.read_bytes())))
    out = root / 'data/materials/default-adapters'
    report = prepare_materials(root, out, gate=NoWaitGate())
    assert report['status'] == 'pending_human_review' and report['blockers'] == []
    _, rows = load_search_candidates(out / 'seed.json')
    assert len(rows) == 3
    book = json.loads((out / 'sources.prepared.json').read_text())
    assert all(s['evidence'] == [] for s in book['sources'].values())
