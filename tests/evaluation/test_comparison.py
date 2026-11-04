"""Public publication boundary: incomplete evidence must stay visible."""
from paper_assistant.evaluation_review import compare_reports


def test_missing_rejected_and_unreviewed_cases_cannot_become_quality_scores():
    suite = {"cases": [{"id": str(i)} for i in range(5)]}
    baseline = {"cases": [{"case_id": "0", "execution_status": "http_rejected"}]}
    candidate = {"cases": [{"case_id": "0", "execution_status": "executed",
                             "evidence_kind": "controlled_web_test", "review": {"verdict": "pass"}}]}
    report = compare_reports(baseline, candidate, suite)
    assert len(report["cases"]) == 5
    assert report["quality_pass_rate"] is None
    assert report["status"] == "blocked"
    assert report["cases"][1]["candidate"]["status"] == "blocked"


def reviewed_case():
    return {"case_id": "0", "mode": "fixed", "input": {"max_cost_usd": .1},
            "execution_status": "executed", "evidence_kind": "real_model_http",
            "review": {"reviewer": "Reviewer A", "reviewed_at": "2026-11-04T12:00:00Z",
                       "verdict": "pass", "failure_reasons": [], "checks": {key: "pass" for key in ("task", "agent_behavior", "traceability", "support", "execution_boundaries")},
                       "unobserved_branches": ["download_failure"],
                       "claims": [{"turn_id": "research", "claim": "method", "source_version": "paper-v1",
                                   "page_number": 1, "quote": "source", "support": "supports", "reason": "direct description"}]},
            "local_artifacts": {"research": {"binding": {"policy_id": "old"},
                "model_events": [{"simulated": False, "charged_tokens": 10, "charged_cost_usd": .01,
                                  "usage_unknown": False, "metadata": {"request_submitted": True, "returned_model": "model"}}],
                "tool_trace": []}}}


def test_empty_review_cannot_pass_and_unrelated_citation_overrides_pass():
    from paper_assistant.evaluation_review import assess_case
    case = reviewed_case()
    case["review"]["claims"] = []
    assert assess_case(case)["status"] == "pending"
    case = reviewed_case()
    case["review"]["claims"][0]["support"] = "unrelated"
    assert assess_case(case)["status"] == "fail"
    assert "hard_failure:unrelated" in assess_case(case)["reasons"]


def test_report_deduplicates_snapshot_turns_and_does_not_sum_state_and_events():
    from paper_assistant.evaluation_review import assess_case
    case = reviewed_case()
    turn = {"id": "turn-1", "usage": {"tokens": 200, "cost_usd": .2}}
    case["research"] = {"state": {"usage": {"tokens": 900, "cost_usd": .9}}}
    case["followups"] = [{"snapshot": {"turns": [turn]}}, {"snapshot": {"turns": [turn]}}]
    case["local_artifacts"]["turn-1"] = {"model_events": [
        {"simulated": False, "charged_tokens": 20, "charged_cost_usd": .02,
         "usage_unknown": True, "metadata": {"request_submitted": True}}]}
    result = assess_case(case)
    assert result["accounting"]["charged_tokens"] == 30
    assert result["accounting"]["charged_or_reserved_usd"] == .03
    assert result["accounting"]["unknown_reserved_usd"] == .02
    assert len(result["rounds"]) == 2


def test_strategy_change_is_visible_but_same_conditions_can_be_compared():
    import copy
    baseline = {"mode": "fixed", "model_config": {"model": "model"}, "frozen_inputs": {
        "sha256": {"suite.json": "suite", "sources.json": "source", "rubric.md": "rubric"}},
        "cases": [reviewed_case()]}
    candidate = copy.deepcopy(baseline)
    candidate["cases"][0]["local_artifacts"]["research"]["binding"]["policy_id"] = "new"
    report = compare_reports(baseline, candidate, {"cases": [{"id": "0"}]})
    assert report["cases"][0]["comparable"] is True
    assert report["cases"][0]["strategy_changed"] is True
    assert report["quality_pass_rate"] is None  # exactly five cases are required
    candidate["cases"][0]["input"]["max_cost_usd"] = .5
    report = compare_reports(baseline, candidate, {"cases": [{"id": "0"}]})
    assert report["cases"][0]["comparable"] is False
    assert "actual_inputs_or_budgets_differ" in report["cases"][0]["condition_differences"]


def test_five_recorded_results_require_every_turn_and_rubric_before_acceptance():
    import copy
    case = reviewed_case()
    base = {"mode": "fixed", "model_config": {"model": "model"}, "frozen_inputs": {"sha256": {
        "suite.json": "suite", "sources.json": "sources", "rubric.md": "rubric"}}, "cases": []}
    for i in range(5):
        row = copy.deepcopy(case)
        row["case_id"] = str(i)
        base["cases"].append(row)
    suite = {"cases": [{"id": str(i)} for i in range(5)]}
    report = compare_reports(base, copy.deepcopy(base), suite)
    assert report["quality_pass_rate"] == 1
    candidate = copy.deepcopy(base)
    candidate["cases"][0]["followups"] = [{"snapshot": {"turns": [{"id": "t2"}]}}]
    report = compare_reports(base, candidate, suite)
    assert report["quality_pass_rate"] is None
    assert "review_missing_turn:t2" in report["cases"][0]["candidate"]["reasons"]


def test_live_origin_cache_is_reported_as_cache_without_network_success():
    from paper_assistant.evaluation_review import assess_case
    case = reviewed_case()
    case["local_artifacts"]["research"]["tool_trace"] = [{"tool": "fetch_paper", "result": {
        "success": True, "data": {"from_cache": True, "asset": {"origin": "live_arxiv_pdf"}}}}]
    result = assess_case(case)
    assert result["rounds"][0]["cache_records"][0]["from_cache"] is True
    assert result["online_network_verified"] is False


def test_matching_but_changed_frozen_requests_and_missing_followups_are_blocked():
    import copy
    case = reviewed_case()
    base = {"mode": "fixed", "cases": [case]}
    spec = {"id": "0", "web_payload": {"max_cost_usd": .1}, "followups": ["Which method?"]}
    report = compare_reports(base, copy.deepcopy(base), {"cases": [spec]})
    assert "candidate:frozen_actual_inputs_mismatch" in report["cases"][0]["condition_differences"]


def test_duplicate_case_ids_cannot_silently_choose_first_record():
    import copy
    case = reviewed_case()
    base = {"mode": "fixed", "cases": [case, copy.deepcopy(case)]}
    report = compare_reports(base, copy.deepcopy(base), {"cases": [{"id": "0"}]})
    assert "candidate:duplicate_case_id" in report["cases"][0]["condition_differences"]


def test_human_checks_cannot_omit_unobserved_branches_or_use_unknown_values():
    from paper_assistant.evaluation_review import assess_case
    case = reviewed_case()
    del case["review"]["unobserved_branches"]
    assert assess_case(case)["status"] == "pending"
    case = reviewed_case()
    case["review"]["verdict"] = "fail"
    case["review"]["failure_reasons"] = ["bad evidence"]
    case["review"]["checks"]["task"] = "anything"
    assert assess_case(case)["eligible_for_quality_score"] is False


def test_pass_verdict_conflicting_with_failed_human_check_is_failure():
    from paper_assistant.evaluation_review import assess_case
    case = reviewed_case()
    case["review"]["checks"]["support"] = "fail"
    assert assess_case(case)["status"] == "fail"
