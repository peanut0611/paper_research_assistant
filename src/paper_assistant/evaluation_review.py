"""Validate human reviews and publish conservative, reproducible five-case comparisons.

This is an evidence validator, not a semantic judge or reviewer identity service.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path

from paper_assistant.evaluation import save_json, file_hash

RUBRIC_AXES = {"task", "agent_behavior", "traceability", "support", "execution_boundaries"}

HARD_FAILURES = {"fabricated", "unrelated", "wrong_source", "fabricated_paper", "fabricated_citation",
                 "fabricated_page", "business_cost_substitution", "unsupported_exhaustiveness"}


def validate_review(review):
    claims = review.get("claims") or []
    hard = ["hard_failure:" + str(c.get("support")) for c in claims if c.get("support") in HARD_FAILURES]
    hard += ["hard_failure:" + str(x) for x in review.get("hard_failures", [])]
    errors = []
    if review.get("verdict") not in ("pass", "fail"):
        errors.append("human_review_pending")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        errors.append("reviewer_missing")
    try:
        if datetime.fromisoformat(review.get("reviewed_at", "").replace("Z", "+00:00")).tzinfo is None:
            errors.append("review_time_timezone_missing")
    except (TypeError, ValueError):
        errors.append("review_time_missing")
    if not claims:
        errors.append("claims_missing")
    for claim in claims:
        if (any(not claim.get(key) for key in ("turn_id", "claim", "source_version", "quote", "reason"))
                or type(claim.get("page_number")) is not int or claim["page_number"] < 1
                or claim.get("support") not in ({"supports", "partial", "unsupported"} | HARD_FAILURES)):
            errors.append("claim_evidence_incomplete")
    if not RUBRIC_AXES.issubset(review.get("checks", {})):
        errors.append("rubric_checks_missing")
    if any(value not in ("pass", "fail") for value in review.get("checks", {}).values()):
        errors.append("rubric_check_value_invalid")
    if not isinstance(review.get("unobserved_branches"), list):
        errors.append("unobserved_branches_missing")
    if review.get("verdict") == "pass" and any(value != "pass" for value in review.get("checks", {}).values()):
        errors.append("rubric_checks_not_passed")
    if review.get("verdict") == "fail" and not review.get("failure_reasons"):
        errors.append("failure_reasons_missing")
    if not hard and review.get("verdict") == "pass" and (review.get("failure_reasons")
            or "fail" in review.get("checks", {}).values()
            or any(c.get("support") != "supports" for c in claims)):
        return "fail", ["review_pass_contradicts_evidence"] + [
            e for e in errors if e != "rubric_checks_not_passed"]
    if hard:
        return "fail", hard + errors
    return ("pending" if errors else review["verdict"]), errors


def _cache_records(value, path=""):
    records = []
    if isinstance(value, dict):
        if "from_cache" in value:
            records.append({"path": path, **{k: value[k] for k in (
                "from_cache", "stale_cache", "snapshot_origin", "origin", "source_sha256", "raw_sha256") if k in value}})
        for key, item in value.items():
            records.extend(_cache_records(item, path + "/" + str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            records.extend(_cache_records(item, path + "/" + str(index)))
    return records


def _rounds(case):
    research = case.get("research") or {}
    snapshots = {"research": research.get("state") or {}}
    for row in case.get("followups", []):
        for turn in (row.get("snapshot") or {}).get("turns", []):
            snapshots[turn["id"]] = turn  # latest snapshot, one charge per persisted turn
    artifacts = case.get("local_artifacts", {})
    for identifier in artifacts:
        snapshots.setdefault(identifier, {})
    rows = []
    for identifier, state in snapshots.items():
        artifact = artifacts.get(identifier, {})
        events = artifact.get("model_events", [])
        known = bool(events)
        rows.append({"round_id": identifier, "stop_reason": artifact.get("stop_reason", state.get("stop_reason")),
            "elapsed_seconds": artifact.get("elapsed_seconds", state.get("elapsed_seconds")), "accounting_source": "model_events" if known else "unavailable",
            "charged_tokens": sum(e.get("charged_tokens", 0) for e in events) if known else None,
            "charged_or_reserved_usd": round(sum(e.get("charged_cost_usd", 0) for e in events), 10) if known else None,
            "unknown_reserved_usd": round(sum(e.get("charged_cost_usd", 0) for e in events if e.get("usage_unknown")), 10) if known else None,
            "unknown_usage_calls": sum(bool(e.get("usage_unknown")) for e in events) if known else None,
            "input_tokens": sum((e.get("usage") or {}).get("input_tokens", 0) for e in events) if known else None,
            "output_tokens": sum((e.get("usage") or {}).get("output_tokens", 0) for e in events) if known else None,
            "cached_input_tokens": sum(e.get("metadata", {}).get("cached_input_tokens", 0) for e in events) if known else None,
            "workspace_identity": artifact.get("workspace_identity"),
            "model_events": events, "binding": artifact.get("binding"), "tool_trace": artifact.get("tool_trace"),
            "cache_records": _cache_records(artifact.get("tool_trace", [])),
            "state_usage_reference_only": state.get("usage"), "materials": state.get("candidates", state.get("documents", state.get("papers"))),
            "agent_events": state.get("agent_events", []), "answer": state.get("evidence_answer", state.get("answer"))})
    return rows


def assess_case(case):
    if not case:
        return {"status": "blocked", "reasons": ["case_missing"], "quality_pass_rate": None}
    blockers = list(case.get("blockers", []))
    if case.get("execution_status") != "executed":
        blockers.append("execution_not_complete:" + case.get("execution_status", "missing"))
    if case.get("evidence_kind") != "real_model_http":
        blockers.append("not_real_model_evidence")
    rounds = _rounds(case)
    events = [e for row in rounds for e in row["model_events"]]
    if not events or any(e.get("simulated") is not False or not e.get("metadata", {}).get("request_submitted") for e in events):
        blockers.append("real_model_events_not_verified")
    if any(not row["model_events"] for row in rounds):
        blockers.append("round_model_accounting_missing")
    if any(row["tool_trace"] is None for row in rounds):
        blockers.append("tool_trace_missing")
    verdict, errors = validate_review(case.get("review", {}))
    reviewed_turns = {c.get("turn_id") for c in case.get("review", {}).get("claims", [])}
    for row in rounds:
        if row["round_id"] not in reviewed_turns:
            errors.append("review_missing_turn:" + row["round_id"])
    if errors and verdict == "pass":
        verdict = "pending"
    network_tools = set()
    materials = {}
    for row in rounds:
        trace = row["tool_trace"] or []
        if isinstance(trace, dict):
            trace = trace.get("events", [])
        for event in trace:
            tool = event.get("tool")
            result = event.get("result") or {}
            data = result.get("data") or {}
            asset = data.get("asset") or {}
            if asset:
                identity = {key: asset.get(key) for key in ("arxiv_id", "version", "source_sha256", "origin")}
                materials[json.dumps(identity, sort_keys=True)] = identity
            origin = data.get("snapshot_origin", (data.get("asset") or {}).get("origin", ""))
            if result.get("success") is True and data.get("from_cache") is False and str(origin).startswith("live_arxiv"):
                network_tools.add(tool)
    online_verified = "search_literature" in network_tools and "fetch_paper" in network_tools
    if case.get("mode") == "online" and not online_verified:
        blockers.append("online_search_and_download_not_verified")
    # A recorded hard failure remains a failure even alongside contradictory pass or blockers.
    status = "fail" if verdict == "fail" else "blocked" if blockers else verdict
    totals = {key: (round(sum(row[key] or 0 for row in rounds), 10) if events else None)
              for key in ("charged_tokens", "charged_or_reserved_usd", "unknown_reserved_usd", "unknown_usage_calls", "input_tokens", "output_tokens", "cached_input_tokens")}
    totals["complete"] = all(row["accounting_source"] == "model_events" for row in rounds)
    return {"status": status, "eligible_for_quality_score": not blockers and verdict in ("pass", "fail") and all(e.startswith("hard_failure:") or e == "review_pass_contradicts_evidence" for e in errors), "reasons": blockers + errors, "execution_status": case.get("execution_status"),
            "review_verdict": case.get("review", {}).get("verdict", "not_reviewed"), "review": case.get("review"),
            "actual_materials": list(materials.values()), "online_network_verified": online_verified,
            "collection_elapsed_seconds": case.get("collection_elapsed_seconds"),
            "started_at": case.get("started_at"), "finished_at": case.get("finished_at"),
            "rounds": rounds, "accounting": totals, "quality_pass_rate": None}


def _inputs(case):
    def clean(value):
        return {k: v for k, v in value.items() if k != "request_id"}
    return {"research": clean(case.get("input", {})),
            "followups": [clean(row.get("input", {})) for row in case.get("followups", [])]}


def compare_reports(baseline, candidate, suite):
    cases = []
    common = []
    for name in ("suite.json", "sources.json", "rubric.md"):
        a = baseline.get("frozen_inputs", {}).get("sha256", {}).get(name)
        b = candidate.get("frozen_inputs", {}).get("sha256", {}).get(name)
        if not a or a != b:
            common.append("frozen_condition_missing_or_different:" + name)
    for key in ("mode", "model_config"):
        if baseline.get(key) is None or baseline.get(key) != candidate.get(key):
            common.append("missing_or_different:" + key)
    for spec in suite["cases"]:
        identifier = spec["id"]
        before = next((c for c in baseline.get("cases", []) if c["case_id"] == identifier), None)
        after = next((c for c in candidate.get("cases", []) if c["case_id"] == identifier), None)
        differences = list(common)
        for side, report, case in (("baseline", baseline, before), ("candidate", candidate, after)):
            if sum(c["case_id"] == identifier for c in report.get("cases", [])) > 1:
                differences.append(side + ":duplicate_case_id")
            if case and case.get("mode") != report.get("mode"):
                differences.append(side + ":case_mode_mismatch")
            if case and "web_payload" in spec:
                expected = {"research": {**spec["web_payload"], "source_mode":
                    "online" if report.get("mode") == "online" else "cached_example"},
                    "followups": [{"question": q, "max_cost_usd": .05} for q in spec.get("followups", [])]}
                if _inputs(case) != expected:
                    differences.append(side + ":frozen_actual_inputs_mismatch")
        if before is None or after is None:
            differences.append("case_missing")
        elif _inputs(before) != _inputs(after):
            differences.append("actual_inputs_or_budgets_differ")
        a, b = assess_case(before), assess_case(after)
        models = lambda result: sorted({json.dumps({k: e.get("metadata", {}).get(k) for k in (
            "requested_model", "returned_model", "reasoning_effort")}, sort_keys=True)
            for row in result.get("rounds", []) for e in row["model_events"]})
        if models(a) != models(b):
            differences.append("actual_model_settings_differ")
        if a.get("actual_materials", []) != b.get("actual_materials", []):
            differences.append("actual_paper_versions_or_bytes_differ")
        bindings = lambda result: [{k: v for k, v in (r["binding"] or {}).items() if k not in ("task_id", "task")} for r in result.get("rounds", [])]
        cases.append({"case_id": identifier, "baseline": a, "candidate": b,
                      "comparable": not differences, "condition_differences": differences,
                      "strategy_changed": bindings(a) != bindings(b),
                      "processing_changed": [r["workspace_identity"] for r in a.get("rounds", [])] != [r["workspace_identity"] for r in b.get("rounds", [])],
                      "actual_inputs": {"baseline": _inputs(before or {}), "candidate": _inputs(after or {})}})
    ready = len({c["case_id"] for c in cases}) == len(cases) and len(cases) == 5 and all(c["comparable"] and all(c[s].get("eligible_for_quality_score", False)
                                                   for s in ("baseline", "candidate")) for c in cases)
    rate = sum(c["candidate"]["status"] == "pass" for c in cases) / 5 if ready else None
    return {"schema_version": 1, "status": ("accepted" if rate >= .8 else "failed") if ready else "blocked",
            "quality_pass_rate": rate, "baseline_quality_pass_rate": sum(c["baseline"]["status"] == "pass" for c in cases) / 5 if ready else None,
            "cases": cases, "code_snapshots": {"baseline": baseline.get("workspace"), "candidate": candidate.get("workspace")},
            "limitations": ["Five cases are not a population accuracy estimate or statistically significant improvement.",
                            "Reviewer identity and semantic support require human verification; validator checks recorded evidence only.",
                            "origin=live does not prove network success in this run; inspect from_cache in tool traces.",
                            "Lifecycle/default-budget automated tests are separate from frozen-budget model quality acceptance."]}


def markdown(report):
    rows = ["# Agent V1 逐例对比", "", f"状态：{report['status']}；质量通过率：{report['quality_pass_rate']}", "",
            "| 案例 | 基线 | 改进版 | 同条件 | 策略变化 |", "|---|---|---|---|---|"]
    for case in report["cases"]:
        rows.append(f"| {case['case_id']} | {case['baseline']['status']} | {case['candidate']['status']} | {case['comparable']} | {case['strategy_changed']} |")
        for side in ("baseline", "candidate"):
            item = case[side]
            rows.extend(["", f"{case['case_id']} / {side}: " + "; ".join(item["reasons"]),
                         "", "用量（含未知预留）：`" + json.dumps(item.get("accounting"), ensure_ascii=False) + "`"])
            for row in item.get("rounds", []):
                rows.extend(["", f"轮次 {row['round_id']}；停止：{row['stop_reason']}；耗时：{row['elapsed_seconds']} 秒；token：{row['charged_tokens']}；费用/预留：{row['charged_or_reserved_usd']}；未知预留：{row['unknown_reserved_usd']}",
                             "", "材料：`" + json.dumps(row["materials"], ensure_ascii=False) + "`",
                             "", "缓存来源：`" + json.dumps(row["cache_records"], ensure_ascii=False) + "`"])
        if case["condition_differences"]:
            rows.extend(["", "条件差异：" + "; ".join(case["condition_differences"])])
    rows += ["", *report["limitations"]]
    return "\n".join(rows) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("compare", "review"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path, required=True, help="capture directory (report.json)")
    parser.add_argument("--suite", type=Path, default=Path("eval_data/agent_v1/suite.json"))
    parser.add_argument("--reviews", type=Path, help="JSON mapping case IDs to human reviews; never edits raw capture")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        candidate = json.loads((args.candidate / "report.json").read_text(encoding="utf-8"))
        if args.reviews:
            reviews = json.loads(args.reviews.read_text(encoding="utf-8"))
            unknown = set(reviews) - {c["case_id"] for c in candidate["cases"]}
            if unknown:
                raise ValueError("Review case IDs absent from capture")
            for case in candidate["cases"]:
                if case["case_id"] in reviews:
                    case["review"] = reviews[case["case_id"]]
        args.out.mkdir(parents=True, exist_ok=False)
        if args.command == "review":
            save_json(args.out / "report.json", candidate)
            save_json(args.out / "review-validation.json", {c["case_id"]: assess_case(c) for c in candidate["cases"]})
            return
        if not args.baseline:
            raise ValueError("compare requires --baseline")
        baseline = json.loads((args.baseline / "report.json").read_text(encoding="utf-8"))
        report = compare_reports(baseline, candidate, json.loads(args.suite.read_text(encoding="utf-8")))
        expected_suite = file_hash(args.suite)
        if any(r.get("frozen_inputs", {}).get("sha256", {}).get("suite.json") != expected_suite for r in (baseline, candidate)):
            report.update(status="blocked", quality_pass_rate=None, baseline_quality_pass_rate=None)
            report["limitations"].append("Provided suite does not match captured frozen suite SHA; comparison is blocked.")
        save_json(args.out / "comparison.json", report)
        (args.out / "comparison.md").write_text(markdown(report))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Evidence validation stopped: {type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
