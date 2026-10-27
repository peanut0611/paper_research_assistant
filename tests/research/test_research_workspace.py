import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from paper_assistant.tools.knowledge.embedding import load_dense_index
from paper_assistant.tools.arxiv.fetch import PaperFetcher
from paper_assistant.tools.knowledge.pdf_parser import PageDocument, parse_pdf
from paper_assistant.research.workspace import ExtractedPDF, ResearchWorkspace, WorkspaceConfig, WorkspaceError, parse_pdf_isolated
from tests.tools.test_paper_fetcher import FakeResponse, candidates, search_report, session, task
from tests.tools.test_token_chunking import CharacterTokenizer


class WorkspaceEmbedder:
    max_sequence_length = 512
    dimension = 3
    document_prefix = "passage: "
    query_prefix = "query: "
    tokenizer = CharacterTokenizer()
    cache_identity = {"revision": "test-v1", "tokenizer": "unicode-character-v1"}

    def __init__(self, model_name):
        self.model_name = model_name
        self.document_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        return np.asarray([[1, "Retrieval" in text, "page 2" in text] for text in texts], dtype=np.float32)

    def embed_query(self, text):
        return np.asarray([1, "retrieval" in text.lower(), "page 2" in text], dtype=np.float32)


def local_parser(path, config):
    return ExtractedPDF(tuple(parse_pdf(path, max_pages=config.max_pdf_pages, max_text_chars=config.max_text_chars)), ("Synthetic extraction warning",))


def warmed(tmp_path, sample_pdf, *, count=1):
    sess = session(tmp_path, [FakeResponse(sample_pdf.read_bytes()) for _ in range(count)])
    for candidate in candidates()[:count]:
        assert sess.fetch(candidate.arxiv_id).status == "downloaded"
    return sess.fetcher


def workspace(tmp_path, fetcher, *, task_id="research-test", rows=None, **kwargs):
    return ResearchWorkspace(task_id, task(), rows or candidates(), root=tmp_path / "runs", fetcher=fetcher,
                             embedder_factory=kwargs.pop("embedder_factory", WorkspaceEmbedder),
                             parser=kwargs.pop("parser", local_parser), **kwargs)


def first_paper_record(ws):
    return next(row for row in ws.list_documents() if row["arxiv_id"] == candidates()[0].arxiv_id)


def test_indexing_preserves_canonical_identity_pages_offsets_and_source_links(tmp_path, sample_pdf):
    ws = workspace(tmp_path, warmed(tmp_path, sample_pdf))
    assert all(row["status"] == "abstract_only" for row in ws.list_documents())
    result = ws.ingest_paper(candidates()[0].arxiv_id)
    assert result["status"] == "fulltext_ready" and not result["reused"]
    index = load_dense_index(ws.index_directory)
    assert {chunk.document_id for chunk in index.chunks} == {"arxiv--2401.00544--v2"}
    read = ws.read_pages(result["document_id"], 1, max_chars=10)
    assert read["truncated"] and read["source"]["arxiv_id"] == "2401.00544" and read["source"]["version"] == 2
    assert read["evidence_type"] == "fulltext" and read["source"]["pdf_url"].endswith("2401.00544v2")
    assert read["parse_warnings"] == ("Synthetic extraction warning",)
    text = ws.read_pages(result["document_id"], 1)["pages"][0]["text"]
    for chunk in index.chunks:
        source = ws.read_pages(chunk.document_id, chunk.page_number)["pages"][0]["text"]
        assert source[chunk.char_start:chunk.char_end] == chunk.text
    assert text and ws.fetcher.network_requests == 1  # Warming only; ingestion is offline.


@pytest.mark.parametrize("method", ["dense", "bm25", "hybrid"])
def test_task_scoped_retrieval_preserves_provenance_and_rejects_foreign_ids(tmp_path, sample_pdf, method):
    ws = workspace(tmp_path, warmed(tmp_path, sample_pdf, count=2))
    first = ws.ingest_paper(candidates()[0].arxiv_id)
    second = ws.ingest_paper(candidates()[1].arxiv_id)
    results = ws.search_documents("retrieval page 2", method=method, document_ids=[second["document_id"]])
    assert results["results"]
    assert all(row["source"]["arxiv_id"] == candidates()[1].arxiv_id for row in results["results"])
    assert results["results"][0]["chunk"]["char_end"] is not None
    assert ws.search_documents("retrieval", method=method, document_ids=[])["results"] == []
    with pytest.raises(ValueError):
        ws.search_documents("retrieval", method=method, document_ids=["paper"])
    assert first["document_id"] != second["document_id"]


def test_resume_reuses_valid_pages_and_vectors_without_parsing_or_encoding(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf)
    ws = workspace(tmp_path, fetcher)
    event = ws.ingest_paper(candidates()[0].arxiv_id)
    old_index = ws.index_directory
    def fail_parser(*args):
        raise AssertionError("Resume should not parse again")
    class ReuseEmbedder(WorkspaceEmbedder):
        def embed_documents(self, texts):
            raise AssertionError("Resume should not encode again")
    resumed = workspace(tmp_path, fetcher, parser=fail_parser, embedder_factory=ReuseEmbedder)
    assert resumed.ingest_paper(candidates()[0].arxiv_id)["reused"]
    assert resumed.index_directory == old_index
    assert resumed.read_pages(event["document_id"], 1)["pages"]


def test_two_tasks_do_not_mix_documents_even_with_shared_pdf_cache(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf, count=2)
    first = workspace(tmp_path, fetcher, task_id="one")
    second = workspace(tmp_path, fetcher, task_id="two")
    first.ingest_paper(candidates()[0].arxiv_id)
    second.ingest_paper(candidates()[1].arxiv_id)
    assert {chunk.document_id for chunk in load_dense_index(first.index_directory).chunks} == {"arxiv--2401.00544--v2"}
    assert {chunk.document_id for chunk in load_dense_index(second.index_directory).chunks} == {"arxiv--2401.00396--v2"}
    with pytest.raises(ValueError):
        first.read_pages("arxiv--2401.00396--v2", 1)


def test_missing_cache_never_downloads_or_loads_model(tmp_path):
    def fail_model(*args):
        raise AssertionError("No PDF means no embedding model")
    fetcher = PaperFetcher(cache_directory=tmp_path / "empty")
    ws = workspace(tmp_path, fetcher, embedder_factory=fail_model)
    result = ws.ingest_paper(candidates()[0].arxiv_id)
    assert result["error_code"] == "cache_miss"
    assert first_paper_record(ws)["status"] == "abstract_only"
    assert ws.index_directory is None and ws.search_documents("question")["results"] == []
    assert fetcher.network_requests == 0


def test_blank_text_does_not_become_fulltext_ready_or_trigger_embedding(tmp_path, sample_pdf):
    def blank(path, config):
        return ExtractedPDF((PageDocument("old", "old.pdf", 1, ""),))
    def fail_model(*args):
        raise AssertionError("Empty pages should not load embeddings")
    ws = workspace(tmp_path, warmed(tmp_path, sample_pdf), parser=blank, embedder_factory=fail_model)
    result = ws.ingest_paper(candidates()[0].arxiv_id)
    assert result["error_code"] == "no_text_layer"
    assert first_paper_record(ws)["status"] == "unusable" and ws.index_directory is None


def test_failed_second_paper_keeps_first_index_and_can_retry(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf, count=2)
    ws = workspace(tmp_path, fetcher)
    first = ws.ingest_paper(candidates()[0].arxiv_id)
    index = ws.index_directory
    def fail(*args):
        raise WorkspaceError("parse_timeout", "Synthetic timeout")
    ws._parser = fail
    failed = ws.ingest_paper(candidates()[1].arxiv_id)
    assert failed["error_code"] == "parse_timeout" and ws.index_directory == index
    assert ws.read_pages(first["document_id"], 1)["pages"]
    ws._parser = local_parser
    assert ws.ingest_paper(candidates()[1].arxiv_id)["status"] == "fulltext_ready"
    assert len({chunk.document_id for chunk in load_dense_index(ws.index_directory).chunks}) == 2


def test_page_limits_corrupt_pdf_and_text_limit_are_explicit_worker_errors(tmp_path, sample_pdf):
    with pytest.raises(WorkspaceError) as caught:
        parse_pdf_isolated(sample_pdf, WorkspaceConfig(max_pdf_pages=1))
    assert caught.value.code == "page_limit_exceeded"
    with pytest.raises(WorkspaceError) as caught:
        parse_pdf_isolated(sample_pdf, WorkspaceConfig(max_text_chars=10))
    assert caught.value.code == "text_limit_exceeded"
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\ninvalid\n%%EOF")
    with pytest.raises(WorkspaceError) as caught:
        parse_pdf_isolated(broken, WorkspaceConfig())
    assert caught.value.code == "parse_failed"


def test_actual_worker_can_extract_and_timeout_is_enforced(tmp_path, sample_pdf):
    parsed = parse_pdf_isolated(sample_pdf, WorkspaceConfig())
    assert len(parsed.pages) == 2 and "Hybrid Retrieval" in parsed.pages[0].text
    with pytest.raises(WorkspaceError) as caught:
        parse_pdf_isolated(sample_pdf, WorkspaceConfig(parse_timeout_seconds=0.0001))
    assert caught.value.code == "parse_timeout"


def test_subprocess_timeout_is_passed_and_no_thread_is_left_running(tmp_path, sample_pdf, monkeypatch):
    import paper_assistant.research.workspace as module
    calls = []
    def expire(command, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(module.subprocess, "run", expire)
    with pytest.raises(WorkspaceError, match="deadline"):
        parse_pdf_isolated(sample_pdf, WorkspaceConfig(parse_timeout_seconds=2))
    assert calls[0]["timeout"] == 2


@pytest.mark.parametrize("task_id", ["../escape", "/tmp/task", "CON", "a/b", "", "a" * 65])
def test_invalid_task_ids_cannot_write_outside_task_root(tmp_path, task_id):
    with pytest.raises(ValueError):
        workspace(tmp_path, PaperFetcher(), task_id=task_id)
    assert not (tmp_path / "runs").exists()


def test_task_config_and_version_changes_cannot_reuse_stale_outputs(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf)
    ws = workspace(tmp_path, fetcher)
    ws.ingest_paper(candidates()[0].arxiv_id)
    with pytest.raises(ValueError, match="changed"):
        workspace(tmp_path, fetcher, config=WorkspaceConfig(max_tokens=256))
    rows = list(candidates())
    rows[0] = replace(rows[0], version=3)
    with pytest.raises(ValueError, match="version changed"):
        workspace(tmp_path, fetcher, rows=rows)
    class ChangedModel(WorkspaceEmbedder):
        cache_identity = {"revision": "changed", "tokenizer": "unicode-character-v1"}
    resumed = workspace(tmp_path, fetcher, embedder_factory=ChangedModel)
    assert resumed.ingest_paper(candidates()[0].arxiv_id)["error_code"] == "embedding_config_mismatch"


def test_task_source_or_index_tampering_is_detected(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf)
    ws = workspace(tmp_path, fetcher)
    event = ws.ingest_paper(candidates()[0].arxiv_id)
    source = next((ws.directory / "sources").glob("*.pdf"))
    source.write_bytes(source.read_bytes() + b"\n%changed")
    with pytest.raises(WorkspaceError, match="failed validation"):
        ws.read_pages(event["document_id"], 1)
    with pytest.raises(WorkspaceError):
        workspace(tmp_path, fetcher)


def test_corrupt_external_pdf_cache_cannot_replace_successful_task_snapshot(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf)
    ws = workspace(tmp_path, fetcher)
    event = ws.ingest_paper(candidates()[0].arxiv_id)
    cached = fetcher.cached_paper(candidates()[0])
    Path(cached.pdf_path).write_bytes(Path(cached.pdf_path).read_bytes() + b"\n%changed")
    result = ws.ingest_paper(candidates()[0].arxiv_id)
    assert result["error_code"] == "invalid_cache"
    assert first_paper_record(ws)["status"] == "fulltext_ready"
    assert ws.read_pages(event["document_id"], 1)["pages"]


def test_task_paper_cap_applies_to_cached_assets(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf, count=2)
    ws = ResearchWorkspace("limited", replace(task(), target_papers=1), candidates(), root=tmp_path / "runs",
                           fetcher=fetcher, embedder_factory=WorkspaceEmbedder, parser=local_parser)
    assert ws.ingest_paper(candidates()[0].arxiv_id)["status"] == "fulltext_ready"
    assert ws.ingest_paper(candidates()[1].arxiv_id)["error_code"] == "paper_budget_exhausted"
    assert len(load_dense_index(ws.index_directory).chunks) == 2


def test_embedding_failure_does_not_destroy_previously_indexed_paper(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf, count=2)
    ws = workspace(tmp_path, fetcher)
    good = ws.ingest_paper(candidates()[0].arxiv_id)
    active = ws.index_directory
    def fail(texts):
        raise RuntimeError("Synthetic model failure")
    ws._embedder.embed_documents = fail
    failed = ws.ingest_paper(candidates()[1].arxiv_id)
    assert failed["error_code"] == "embedding_failed"
    assert ws.index_directory == active
    assert ws.read_pages(good["document_id"], 1)["pages"]


def test_combined_snapshot_publish_failure_leaves_old_snapshot_usable_and_retryable(tmp_path, sample_pdf, monkeypatch):
    ws = workspace(tmp_path, warmed(tmp_path, sample_pdf, count=2))
    good = ws.ingest_paper(candidates()[0].arxiv_id)
    active = ws.index_directory
    original = ws._publish_index
    def fail(index, pages, sources, parent):
        if parent == "indexes":
            raise OSError("Synthetic publication failure")
        return original(index, pages, sources, parent)
    monkeypatch.setattr(ws, "_publish_index", fail)
    assert ws.ingest_paper(candidates()[1].arxiv_id)["status"] == "ingestion_failed"
    assert ws.index_directory == active and ws.read_pages(good["document_id"], 1)["pages"]
    monkeypatch.setattr(ws, "_publish_index", original)
    assert ws.ingest_paper(candidates()[1].arxiv_id)["status"] == "fulltext_ready"


def test_index_vector_changes_are_rejected_on_resume(tmp_path, sample_pdf):
    fetcher = warmed(tmp_path, sample_pdf)
    ws = workspace(tmp_path, fetcher)
    event = ws.ingest_paper(candidates()[0].arxiv_id)
    vectors = ws.index_directory / "embeddings.npy"
    values = np.load(vectors)
    values[0, 0] += 0.1
    np.save(vectors, values)
    with pytest.raises(WorkspaceError):
        ws.read_pages(event["document_id"], 1)


def test_empty_and_warning_pages_keep_physical_pdf_page_numbers(tmp_path, sample_pdf):
    def mixed(path, config):
        return ExtractedPDF((PageDocument("d", "d.pdf", 1, ""), PageDocument("d", "d.pdf", 2, "中文 retrieval evidence")), ("Limited extraction",))
    ws = workspace(tmp_path, warmed(tmp_path, sample_pdf), parser=mixed)
    event = ws.ingest_paper(candidates()[0].arxiv_id)
    row = first_paper_record(ws)
    assert row["blank_pages"] == [1] and row["warnings"] == ["Limited extraction"]
    assert ws.read_pages(event["document_id"], 1)["pages"][0]["text"] == ""
    assert {chunk.page_number for chunk in load_dense_index(ws.index_directory).chunks} == {2}


@pytest.mark.parametrize("data", [[], {}, {"schema_version": 1, "documents": [], "events": []}])
def test_malformed_workspace_manifest_reports_validation_error(tmp_path, data):
    directory = tmp_path / "runs" / "research-test"
    directory.mkdir(parents=True)
    (directory / "workspace.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        workspace(tmp_path, PaperFetcher())


def test_cli_reports_missing_pdf_without_network_or_model_loading(tmp_path):
    report = search_report(tmp_path / "search.json")
    root = Path(__file__).parents[2]
    result = subprocess.run([
        sys.executable, str(root / "scripts" / "dev" / "ingest_research_papers.py"), "--task-id", "empty-cli",
        "--search-results", str(report), "--arxiv-ids", "2401.00396", "--root", str(tmp_path / "runs"),
        "--paper-cache", str(tmp_path / "empty-cache"), "--local-models-only", "--summary",
    ], cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "gbk"},
        capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["events"][0]["error_code"] == "cache_miss"
    assert payload["pdf_network_requests"] == payload["llm_calls"] == 0
