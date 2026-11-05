# Agent V1 逐例对比

状态：blocked；质量通过率：None

| 案例 | 基线 | 改进版 | 同条件 | 策略变化 |
|---|---|---|---|---|
| tool-use-comparison | pending | pending | False | True |

tool-use-comparison / baseline: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 33273, "charged_or_reserved_usd": 0.03388725, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 30891, "output_tokens": 2382, "cached_input_tokens": 0, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：70.131433 秒；token：33273；费用/预留：0.03388725；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/1/result/data", "from_cache": true}]`

tool-use-comparison / candidate: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 31870, "charged_or_reserved_usd": 0.03259875, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 29551, "output_tokens": 2319, "cached_input_tokens": 0, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：85.471621 秒；token：31870；费用/预留：0.03259875；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 17, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/1/result/data", "from_cache": true}, {"path": "/events/4/result/data", "from_cache": true}]`

条件差异：actual_paper_versions_or_bytes_differ
| feedback-and-evidence | pending | pending | False | True |

feedback-and-evidence / baseline: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 26658, "charged_or_reserved_usd": 0.0246069, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 24990, "output_tokens": 1668, "cached_input_tokens": 2432, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：55.892367 秒；token：26658；费用/预留：0.0246069；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/1/result/data", "from_cache": true}]`

feedback-and-evidence / candidate: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 30983, "charged_or_reserved_usd": 0.0320085, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 28644, "output_tokens": 2339, "cached_input_tokens": 0, "complete": true}`

轮次 research；停止：sufficient_evidence；耗时：64.59929 秒；token：30983；费用/预留：0.0320085；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 17, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/0/result/data", "from_cache": true}, {"path": "/events/5/result/data", "from_cache": true}]`

条件差异：actual_paper_versions_or_bytes_differ
| vision-transfer | pending | pending | True | True |

vision-transfer / baseline: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 20665, "charged_or_reserved_usd": 0.0210834, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 18738, "output_tokens": 1927, "cached_input_tokens": 2432, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：50.303719 秒；token：20665；费用/预留：0.0210834；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 21, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/0/result/data", "from_cache": true}]`

vision-transfer / candidate: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 31362, "charged_or_reserved_usd": 0.03407025, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 28549, "output_tokens": 2813, "cached_input_tokens": 0, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：52.75 秒；token：31362；费用/预留：0.03407025；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 21, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/0/result/data", "from_cache": true}]`
| contextual-followups | blocked | pending | False | True |

contextual-followups / baseline: tool_trace_missing; human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research; review_missing_turn:turn-36689c37b74342d2a4c702c0; review_missing_turn:turn-55d06ff378644be192d1264f

用量（含未知预留）：`{"charged_tokens": 67043, "charged_or_reserved_usd": 0.05936865, "unknown_reserved_usd": 0.0151125, "unknown_usage_calls": 1, "input_tokens": 49389, "output_tokens": 3504, "cached_input_tokens": 12672, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：59.222987 秒；token：27371；费用/预留：0.0263754；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/0/result/data", "from_cache": true}]`

轮次 turn-36689c37b74342d2a4c702c0；停止：insufficient_evidence；耗时：35.64100000000326 秒；token：25522；费用/预留：0.01788075；未知预留：0

材料：`null`

缓存来源：`[]`

轮次 turn-55d06ff378644be192d1264f；停止：openai_connection_error；耗时：0.502534 秒；token：14150；费用/预留：0.0151125；未知预留：0.0151125

材料：`null`

缓存来源：`[]`

contextual-followups / candidate: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research; review_missing_turn:turn-f0cfe2c3947a4d558f0ad197; review_missing_turn:turn-dfac21a94dbd440b97f412c6

用量（含未知预留）：`{"charged_tokens": 67437, "charged_or_reserved_usd": 0.0672081, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 62288, "output_tokens": 5149, "cached_input_tokens": 3968, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：64.882996 秒；token：34607；费用/预留：0.0342855；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 17, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/0/result/data", "from_cache": true}, {"path": "/events/4/result/data", "from_cache": true}]`

轮次 turn-f0cfe2c3947a4d558f0ad197；停止：sufficient_evidence；耗时：26.942431 秒；token：13000；费用/预留：0.01218135；未知预留：0

材料：`null`

缓存来源：`[]`

轮次 turn-dfac21a94dbd440b97f412c6；停止：sufficient_evidence；耗时：32.068912 秒；token：19830；费用/预留：0.02074125；未知预留：0

材料：`null`

缓存来源：`[]`

条件差异：actual_model_settings_differ; actual_paper_versions_or_bytes_differ
| unsupported-business-cost | pending | pending | True | True |

unsupported-business-cost / baseline: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 26700, "charged_or_reserved_usd": 0.02421465, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 25145, "output_tokens": 1555, "cached_input_tokens": 2432, "complete": true}`

轮次 research；停止：insufficient_evidence；耗时：66.362394 秒；token：26700；费用/预留：0.02421465；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 17, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/0/result/data", "from_cache": true}, {"path": "/events/3/result/data", "from_cache": true}]`

unsupported-business-cost / candidate: human_review_pending; reviewer_missing; review_time_missing; claims_missing; rubric_checks_missing; unobserved_branches_missing; review_missing_turn:research

用量（含未知预留）：`{"charged_tokens": 30641, "charged_or_reserved_usd": 0.03073575, "unknown_reserved_usd": 0, "unknown_usage_calls": 0, "input_tokens": 28573, "output_tokens": 2068, "cached_input_tokens": 0, "complete": true}`

轮次 research；停止：invalid_decision_limit；耗时：64.10999999998603 秒；token：30641；费用/预留：0.03073575；未知预留：0

材料：`[{"arxiv_id": "2010.11929", "version": 1, "document_id": "arxiv--2010.11929--v1", "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2103.00020", "version": 1, "document_id": "arxiv--2103.00020--v1", "title": "Learning Transferable Visual Models From Natural Language Supervision", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}, {"arxiv_id": "2210.03629", "version": 1, "document_id": "arxiv--2210.03629--v1", "title": "ReAct: Synergizing Reasoning and Acting in Language Models", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 31, "warnings": [], "last_error": null}, {"arxiv_id": "2302.04761", "version": 1, "document_id": "arxiv--2302.04761--v1", "title": "Toolformer: Language Models Can Teach Themselves to Use Tools", "status": "fulltext_ready", "fetch_status": "downloaded", "page_count": 17, "warnings": [], "last_error": null}, {"arxiv_id": "2303.11366", "version": 1, "document_id": "arxiv--2303.11366--v1", "title": "Reflexion: an autonomous agent with dynamic memory and self-reflection", "status": "abstract_only", "fetch_status": null, "page_count": null, "warnings": [], "last_error": null}]`

缓存来源：`[{"path": "/events/1/result/data", "from_cache": true}, {"path": "/events/4/result/data", "from_cache": true}]`

Five cases are not a population accuracy estimate or statistically significant improvement.
Reviewer identity and semantic support require human verification; validator checks recorded evidence only.
origin=live does not prove network success in this run; inspect from_cache in tool traces.
Lifecycle/default-budget automated tests are separate from frozen-budget model quality acceptance.
