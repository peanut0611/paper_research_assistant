import json
import runpy
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from paper_assistant.embedding import build_dense_index, save_dense_index
from paper_assistant.retrieval_comparison import compare_retrievers
from test_embedding import FakeEmbedder
from test_evaluation import question, result, write_questions


def test_select_by_quality_and_rotate_repeated_methods():
    questions = [question(), question("q2", "g2")]
    report = compare_retrievers(questions, {
        "dense": lambda q, k: [],
        "bm25": lambda q, k: [result(1)] if q == "q1" else [],
        "hybrid": lambda q, k: [result(1)],
    }, repeats=3)
    assert report["selection"]["method"] == "hybrid"
    assert report["method_order_per_repeat"] == [
        ["dense", "bm25", "hybrid"], ["bm25", "hybrid", "dense"], ["hybrid", "dense", "bm25"],
    ]
    assert report["methods"]["hybrid"]["settings"]["timed_queries"] == 6
    assert len(report["methods"]["hybrid"]["questions"][0]["latency_samples_ms"]) == 3
    assert report["changes_vs_dense"]["bm25"]["evidence_hit@1"]["improved"] == ["q1"]


def test_repeated_ranking_changes_are_rejected():
    calls = []
    def variable(q, k):
        calls.append(q)
        return [result(1)] if len(calls) <= 2 else []
    with pytest.raises(ValueError, match="Non-deterministic"):
        compare_retrievers([question()], {"dense": variable, "bm25": lambda q, k: [], "hybrid": lambda q, k: []}, repeats=2)


def test_metric_winner_and_bilingual_recommendation_remain_separate():
    questions = [question(f"q{i}", f"g{i}") for i in range(3)]
    questions.append(replace(question("en", "en"), language="en"))
    report = compare_retrievers(questions, {
        "dense": lambda q, k: [result(1)] if q == "en" else [],
        "bm25": lambda q, k: [result(1)] if q != "en" else [],
        "hybrid": lambda q, k: [result(1)] if q in ("q0", "en") else [],
    }, repeats=1)
    assert report["selection"]["method"] == "bm25"
    assert report["bilingual_recommendation"]["method"] == "hybrid"
    assert "bm25" not in report["bilingual_recommendation"]["eligible_methods"]


def test_compare_cli_without_model_download(tmp_path, monkeypatch, capsys):
    import paper_assistant.embedding as embedding
    class FakeModel(FakeEmbedder):
        device = "cpu"
    monkeypatch.setattr(embedding, "SentenceTransformerEmbedder", lambda *a, **kw: FakeModel())
    path = tmp_path / "q.jsonl"
    write_questions(path, [asdict(replace(question(), question="retrieval"))])
    index_path = tmp_path / "index"
    save_dense_index(build_dense_index([result(1).chunk], FakeModel()), index_path)
    output = tmp_path / "reports"
    monkeypatch.setattr(sys, "argv", ["compare_retrieval.py", "--questions", str(path), "--index", str(index_path), "--output-dir", str(output), "--repeats", "1"])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/compare_retrieval.py"), run_name="__main__")
    report = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert set(report["methods"]) == {"dense", "bm25", "hybrid"}
    for name in report["methods"]:
        assert (output / f"{name}.json").exists()
    assert (output / "comparison.md").exists()
    assert "Report directory" in capsys.readouterr().out
