# Agent V1 逐例对比

状态：blocked；质量通过率：None

| 案例 | 基线 | 改进版 | 同条件 | 策略变化 |
|---|---|---|---|---|
| tool-use-comparison | blocked | blocked | False | True |

tool-use-comparison / baseline: case_missing

用量（含未知预留）：`null`

tool-use-comparison / candidate: network_policy_blocks:api.openai.com; network_policy_blocks:export.arxiv.org; network_policy_blocks:arxiv.org; api_key_not_ready; execution_not_requested; execution_not_complete:not_executed; real_model_events_not_verified; round_model_accounting_missing; tool_trace_missing; online_search_and_download_not_verified; human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": null, "charged_or_reserved_usd": null, "unknown_reserved_usd": null, "unknown_usage_calls": null, "input_tokens": null, "output_tokens": null, "cached_input_tokens": null, "complete": false}`

轮次 research；停止：None；耗时：None 秒；token：None；费用/预留：None；未知预留：None

材料：`null`

缓存来源：`[]`

条件差异：missing_or_different:mode; missing_or_different:model_config; case_missing
| feedback-and-evidence | blocked | blocked | False | True |

feedback-and-evidence / baseline: case_missing

用量（含未知预留）：`null`

feedback-and-evidence / candidate: network_policy_blocks:api.openai.com; network_policy_blocks:export.arxiv.org; network_policy_blocks:arxiv.org; api_key_not_ready; execution_not_requested; execution_not_complete:not_executed; real_model_events_not_verified; round_model_accounting_missing; tool_trace_missing; online_search_and_download_not_verified; human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": null, "charged_or_reserved_usd": null, "unknown_reserved_usd": null, "unknown_usage_calls": null, "input_tokens": null, "output_tokens": null, "cached_input_tokens": null, "complete": false}`

轮次 research；停止：None；耗时：None 秒；token：None；费用/预留：None；未知预留：None

材料：`null`

缓存来源：`[]`

条件差异：missing_or_different:mode; missing_or_different:model_config; case_missing
| vision-transfer | blocked | blocked | False | True |

vision-transfer / baseline: case_missing

用量（含未知预留）：`null`

vision-transfer / candidate: network_policy_blocks:api.openai.com; network_policy_blocks:export.arxiv.org; network_policy_blocks:arxiv.org; api_key_not_ready; execution_not_requested; execution_not_complete:not_executed; real_model_events_not_verified; round_model_accounting_missing; tool_trace_missing; online_search_and_download_not_verified; human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": null, "charged_or_reserved_usd": null, "unknown_reserved_usd": null, "unknown_usage_calls": null, "input_tokens": null, "output_tokens": null, "cached_input_tokens": null, "complete": false}`

轮次 research；停止：None；耗时：None 秒；token：None；费用/预留：None；未知预留：None

材料：`null`

缓存来源：`[]`

条件差异：missing_or_different:mode; missing_or_different:model_config; case_missing
| contextual-followups | blocked | blocked | False | True |

contextual-followups / baseline: case_missing

用量（含未知预留）：`null`

contextual-followups / candidate: network_policy_blocks:api.openai.com; network_policy_blocks:export.arxiv.org; network_policy_blocks:arxiv.org; api_key_not_ready; execution_not_requested; execution_not_complete:not_executed; real_model_events_not_verified; round_model_accounting_missing; tool_trace_missing; online_search_and_download_not_verified; human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": null, "charged_or_reserved_usd": null, "unknown_reserved_usd": null, "unknown_usage_calls": null, "input_tokens": null, "output_tokens": null, "cached_input_tokens": null, "complete": false}`

轮次 research；停止：None；耗时：None 秒；token：None；费用/预留：None；未知预留：None

材料：`null`

缓存来源：`[]`

条件差异：missing_or_different:mode; missing_or_different:model_config; candidate:frozen_actual_inputs_mismatch; case_missing
| unsupported-business-cost | blocked | blocked | False | True |

unsupported-business-cost / baseline: case_missing

用量（含未知预留）：`null`

unsupported-business-cost / candidate: network_policy_blocks:api.openai.com; network_policy_blocks:export.arxiv.org; network_policy_blocks:arxiv.org; api_key_not_ready; execution_not_requested; execution_not_complete:not_executed; real_model_events_not_verified; round_model_accounting_missing; tool_trace_missing; online_search_and_download_not_verified; human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": null, "charged_or_reserved_usd": null, "unknown_reserved_usd": null, "unknown_usage_calls": null, "input_tokens": null, "output_tokens": null, "cached_input_tokens": null, "complete": false}`

轮次 research；停止：None；耗时：None 秒；token：None；费用/预留：None；未知预留：None

材料：`null`

缓存来源：`[]`

条件差异：missing_or_different:mode; missing_or_different:model_config; case_missing

Five cases are not a population accuracy estimate or statistically significant improvement.
Reviewer identity and semantic support require human verification; validator checks recorded evidence only.
origin=live does not prove network success in this run; inspect from_cache in tool traces.
Lifecycle/default-budget automated tests are separate from frozen-budget model quality acceptance.
