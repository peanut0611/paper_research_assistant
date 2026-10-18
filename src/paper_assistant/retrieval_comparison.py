"""Controlled Dense/BM25/Hybrid comparison without tuning evaluation labels."""

from collections.abc import Callable, Mapping, Sequence
from statistics import mean

from paper_assistant.embedding import SearchResult
from paper_assistant.evaluation import RetrievalQuestion, evaluate_retrieval, summarize_latency


def compare_retrievers(
    questions: Sequence[RetrievalQuestion],
    retrievers: Mapping[str, Callable[[str, int], Sequence[SearchResult]]],
    *, repeats: int = 3,
) -> dict:
    """Rotate method order across repeats; each run has an unmeasured warmup.

    Retrieval must be deterministic. Selection is lexicographic on group-macro
    evidence Hit@1, evidence MRR@5, complete evidence@3, then mean latency.
    Selection is a development-set choice, not a held-out generalization claim.
    """
    if repeats <= 0:
        raise ValueError("repeats must be greater than zero")
    names = list(retrievers)
    if set(names) != {"dense", "bm25", "hybrid"}:
        raise ValueError("Comparison requires dense, bm25, and hybrid retrievers")
    if not any(question.answerable for question in questions):
        raise ValueError("Selection requires answerable questions")
    runs: dict[str, list[dict]] = {name: [] for name in names}
    orders = []
    for repeat in range(repeats):
        order = names[repeat % len(names):] + names[:repeat % len(names)]
        orders.append(order)
        for name in order:
            runs[name].append(evaluate_retrieval(questions, retrievers[name]))
    reports = {}
    for name, samples in runs.items():
        report = samples[0]
        for sample in samples[1:]:
            if sample["summary"]["metrics"] != report["summary"]["metrics"] or any(
                [result["chunk"]["chunk_id"] for result in a["results"]]
                != [result["chunk"]["chunk_id"] for result in b["results"]]
                for a, b in zip(report["questions"], sample["questions"])
            ):
                raise ValueError(f"Non-deterministic ranking across repeats: {name}")
        all_times = []
        for i, row in enumerate(report["questions"]):
            times = [sample["questions"][i]["latency_ms"] for sample in samples]
            all_times.extend(times)
            row["latency_samples_ms"] = times
        # Mutate only after collecting repeat samples (samples[0] is report).
        for row in report["questions"]:
            row["latency_ms"] = mean(row["latency_samples_ms"])
        report["summary"]["latency_all_queries_ms"] = summarize_latency(all_times)
        report["settings"].update({"timing_repeats": repeats, "timed_queries": len(all_times),
                                   "warmup_queries": repeats,
                                   "timing_scope": "per-query retrieval only; no embedding cache, excludes loading, index construction, warmup and evaluation"})
        reports[name] = report
    def selection_key(name):
        summary = reports[name]["summary"]
        metrics = summary["group_macro_metrics"]
        return (metrics["evidence_hit@1"], metrics["evidence_mrr@5"],
                metrics["complete_evidence@3"], -summary["latency_all_queries_ms"]["mean"])
    ordered = sorted(names, key=selection_key, reverse=True)
    # Preserve the predeclared metric winner. A separate usage recommendation
    # makes the bilingual tradeoff explicit rather than silently changing it.
    baseline_languages = reports["dense"]["summary"]["by_language"]
    eligible = [name for name in names if all(
        reports[name]["summary"]["by_language"][language]["metrics"][metric]
        >= subset["metrics"][metric]
        for language, subset in baseline_languages.items()
        for metric in ("evidence_hit@1", "evidence_hit@3")
    )]
    recommended = max(eligible, key=selection_key)
    baseline = {row["id"]: row for row in reports["dense"]["questions"] if row["answerable"]}
    changes = {}
    for name in ("bm25", "hybrid"):
        changes[name] = {}
        for metric in ("evidence_hit@1", "evidence_mrr@5", "complete_evidence@3"):
            changes[name][metric] = {
                "improved": [row["id"] for row in reports[name]["questions"] if row["answerable"] and row["metrics"][metric] > baseline[row["id"]]["metrics"][metric]],
                "regressed": [row["id"] for row in reports[name]["questions"] if row["answerable"] and row["metrics"][metric] < baseline[row["id"]]["metrics"][metric]],
            }
    return {
        "schema_version": 1, "method_order_per_repeat": orders,
        "selection": {"method": ordered[0], "ranking": ordered,
                      "criteria_in_priority_order": ["group_macro evidence_hit@1", "group_macro evidence_mrr@5", "group_macro complete_evidence@3", "lower mean latency"],
                      "scope": "Provisional winner on the current development set; no held-out test or tuning sweep"},
        "bilingual_recommendation": {
            "method": recommended, "eligible_methods": eligible,
            "reason": "For bilingual use, exclude methods regressing any evaluated language's evidence Hit@1 or Hit@3 versus Dense, then use the metric ordering.",
            "scope": "Usage recommendation based on observed language tradeoffs; separate from the predeclared metric winner. Few language samples do not establish generalization.",
        },
        "changes_vs_dense": changes, "methods": reports,
    }
