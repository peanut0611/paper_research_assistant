import json
import runpy
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from paper_assistant.chunking import TextChunk
from paper_assistant.embedding import SearchResult, build_dense_index, save_dense_index
from paper_assistant.evaluation import (
    RelevantSource, RetrievalQuestion, evaluate_retrieval, file_sha256,
    load_questions, normalize_evidence, score_results, verify_metadata,
    write_evaluation_report,
)
from test_embedding import FakeEmbedder


def question(identifier="q1", group="g1", answerable=True):
    return RetrievalQuestion(
        identifier, group, identifier, "zh", "fact", answerable,
        "retrieval" if answerable else None,
        (RelevantSource("a", 1, ("retrieval",)),) if answerable else (),
        "all" if answerable else "none", None if answerable else "not stated",
    )


def result(rank, text="retrieval", doc="a", page=1):
    return SearchResult(rank, 0.5, TextChunk(
        f"{doc}-{page}-{rank}", doc, f"{doc}.pdf", page, rank, 0, len(text.split()), text,
    ))


def test_hand_calculated_metrics_deduplicate_pages_and_respect_document_id():
    q = replace(question(), relevant_sources=(
        RelevantSource("a", 1, ("Ａ B",)), RelevantSource("a", 2, ("second quote",)),
    ))
    results = [result(1, "A B", doc="wrong"), result(2, "A \nB"),
               result(3, "A B"), result(4, "second quote", page=2)]
    scored = score_results(q, results, [1, 3, 5])
    metrics = scored["metrics"]
    assert metrics["page_hit@1"] == metrics["evidence_hit@1"] == 0
    assert metrics["page_recall@3"] == metrics["evidence_coverage@3"] == 0.5
    assert metrics["complete_evidence@3"] == 0
    assert metrics["page_recall@5"] == metrics["complete_evidence@5"] == 1
    assert metrics["page_mrr@5"] == metrics["evidence_mrr@5"] == 0.5
    assert scored["missing_evidence"]["3"] == [{"document_id": "a", "page_number": 2, "quote": "second quote"}]


def test_correct_page_is_not_evidence_and_quotes_cannot_be_concatenated():
    q = replace(question(), relevant_sources=(RelevantSource("a", 1, ("one two",)),))
    metrics = score_results(q, [result(1, "one"), result(2, "two")], [1, 3])["metrics"]
    assert metrics["page_hit@1"] == 1
    assert metrics["evidence_hit@3"] == metrics["evidence_mrr@3"] == 0


def test_multiple_quotes_require_complete_coverage():
    q = replace(question(), relevant_sources=(RelevantSource("a", 1, ("one", "two")),))
    scored = score_results(q, [result(1, "one")], [1])["metrics"]
    assert scored["evidence_hit@1"] == 1
    assert scored["evidence_coverage@1"] == 0.5
    assert scored["complete_evidence@1"] == 0


def test_summary_group_macro_unanswerable_and_warmup_timing():
    questions = [question(), question("q2"), replace(question("q3", "g2"), language="en"), question("q4", "g3", False)]
    calls = []
    def retrieve(query, k):
        calls.append((query, k))
        return [result(1)] if query in ("q1", "q2") else []
    times = iter([0, 0.01, 1, 1.03, 2, 2.02, 3, 3.04])
    report = evaluate_retrieval(questions, retrieve, ks=[3, 1, 3], clock=lambda: next(times))
    summary = report["summary"]
    assert calls == [("q1", 3), ("q1", 3), ("q2", 3), ("q3", 3), ("q4", 3)]
    assert summary["metrics"]["evidence_hit@1"] == pytest.approx(2/3)
    assert summary["group_macro_metrics"]["evidence_hit@1"] == 0.5
    assert summary["by_language"]["en"]["metrics"]["evidence_hit@1"] == 0
    assert summary["latency_all_queries_ms"]["mean"] == pytest.approx(25)
    assert summary["latency_all_queries_ms"]["p95"] == pytest.approx(38.5)
    assert report["questions"][-1]["metrics"] is None
    assert report["unanswerable"]["question_ids"] == ["q4"]
    assert report["unanswerable"]["rejection_metrics"] is None
    assert report["failures"] == [{"id": "q3", "question": "q3", "incomplete_at_k": [1, 3]}]


def test_no_answerable_questions_has_no_invented_metrics():
    report = evaluate_retrieval([question(answerable=False)], lambda q, k: [], warmup=False)
    assert report["summary"]["metrics"] == {}
    assert report["summary"]["group_macro_metrics"] == {}


@pytest.mark.parametrize("ks", [[], [0], [-1], [True]])
def test_invalid_cutoffs(ks):
    with pytest.raises(ValueError, match="positive integers"):
        evaluate_retrieval([question()], lambda q, k: [], ks=ks)


def test_reject_duplicate_results_and_invalid_scores():
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_retrieval([question()], lambda q, k: [result(1), result(1)], warmup=False)
    with pytest.raises(ValueError, match="non-finite"):
        evaluate_retrieval([question()], lambda q, k: [replace(result(1), score=float("nan"))], warmup=False)


def write_questions(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_question_loading_and_unicode_report_round_trip(tmp_path):
    path = tmp_path / "questions.jsonl"
    q = replace(question(), question="中文 retrieval")
    write_questions(path, [asdict(q)])
    assert load_questions(path) == (q,)
    report = evaluate_retrieval([q], lambda q, k: [result(1)], warmup=False)
    output = write_evaluation_report(report, tmp_path / "output" / "report.json")
    assert json.loads(output.read_text(encoding="utf-8"))["questions"][0]["question"] == "中文 retrieval"


@pytest.mark.parametrize("change", [
    {"answerable": "false"}, {"question": " "}, {"relevant_sources": []},
    {"evidence_policy": "any"}, {"answerable": False},
    {"relevant_sources": [{"document_id": "a", "page_number": True, "evidence_quotes": ["x"]}]},
    {"relevant_sources": [{"document_id": "a", "page_number": 1, "evidence_quotes": [" "]}]},
])
def test_reject_malformed_annotation(tmp_path, change):
    path = tmp_path / "questions.jsonl"
    write_questions(path, [{**asdict(question()), **change}])
    with pytest.raises(ValueError, match="questions.jsonl:1"):
        load_questions(path)


def test_duplicate_ids_and_empty_dataset(tmp_path):
    path = tmp_path / "q.jsonl"
    write_questions(path, [asdict(question())] * 2)
    with pytest.raises(ValueError, match="Duplicate question"):
        load_questions(path)
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_questions(path)


def test_metadata_detects_source_or_annotation_changes(tmp_path):
    dataset = tmp_path / "q.jsonl"
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf-source")
    dataset.write_text("labels", encoding="utf-8")
    meta = tmp_path / "q.meta.json"
    payload = {"dataset_sha256": file_sha256(dataset), "sources": [{"path": source.name, "sha256": file_sha256(source)}]}
    meta.write_text(json.dumps(payload), encoding="utf-8")
    assert verify_metadata(dataset, meta, tmp_path) == payload
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Source hash"):
        verify_metadata(dataset, meta, tmp_path)
    dataset.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Dataset hash"):
        verify_metadata(dataset, meta, tmp_path)


def test_cli_end_to_end_without_real_model(tmp_path, monkeypatch, capsys):
    import paper_assistant.embedding as embedding
    class FakeModel(FakeEmbedder):
        device = "cpu"
    monkeypatch.setattr(embedding, "SentenceTransformerEmbedder", lambda *a, **kw: FakeModel())
    dataset = tmp_path / "q.jsonl"
    write_questions(dataset, [asdict(question()), asdict(question("q2", "g2", False))])
    index_path = tmp_path / "index"
    save_dense_index(build_dense_index([result(1).chunk], FakeModel()), index_path)
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["evaluate_retrieval.py", "--questions", str(dataset), "--index", str(index_path), "--output", str(output)])
    # Use keyword-bearing queries so FakeEmbedder returns a non-zero vector.
    rows = [asdict(replace(question(), question="retrieval")), asdict(replace(question("q2", "g2", False), question="vision"))]
    rows.append(asdict(replace(
        question("q3", "g3"), question="retrieval search",
        relevant_sources=(RelevantSource("a", 1, ("retrieval systems",)),),
    )))
    write_questions(dataset, rows)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/evaluate_retrieval.py"), run_name="__main__")
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["metrics"]["evidence_hit@1"] == 0.5
    assert report["summary"]["answerable_count"] == 2
    assert report["diagnostics"]["evidence_unavailable_in_index"][0]["id"] == "q3"
    assert report["run"]["model_name"] == FakeModel.model_name
    assert report["run"]["index_manifest"]["chunk_count"] == 1
    assert "unanswerable=1" in capsys.readouterr().out


def test_repository_annotations_are_loadable():
    path = Path(__file__).resolve().parents[1] / "eval_data/retrieval_questions.jsonl"
    questions = load_questions(path)
    assert questions
    assert normalize_evidence("Ａ \nB") == "AB"
