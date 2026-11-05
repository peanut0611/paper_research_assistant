# 2026-11-05 修复后五例真实复测

**状态：人工待审；比较 blocked，quality_pass_rate=null，#1/#2/#8 保持打开。**

本轮只运行新版固定材料五例与连续两轮追问，未另跑在线付费案例。run ID 为 `candidate-fixed-20261105-002`；实际源码为 `6f12ed60e1058ddbac15bb9e0c38e950a256219e`。远端分支 `codex/agent-v1-delivery-20261105` 的 `7dcd5deaf3065c14a9e07630dcc2d057055d6750` 是同树发布（tree `550ffa643a484d154c685840b2657c2a298ff0bf`），不是另一次模型实测。

同一 suite/sources/rubric 冻结哈希、同字节全局 seed、固定 PDF 缓存与 E5 权重；GPT-5.4-mini，max_input_tokens=16000、max_output_tokens=1200、timeout_seconds=45、trust_env=true、reasoning effort none。SDK max_retries=0。初始研究每例 $0.10、追问每轮 $0.05，配置额度合计 $0.60，未提高预算、生成上限或评分标准。策略宿主及提示变化保存在实际 binding 中。

总计 **192293 token，估算费用 $0.19662135**；未知用量/预留 $0，未知请求重放 0。费用按持久化轮次去重，不是供应商账单核对。

|案例|初始停止原因|入库全文数|取得正文证据的文档数|含追问费用 USD|
|---|---|---:|---:|---:|
|tool-use-comparison|insufficient_evidence|2|2|0.03259875|
|feedback-and-evidence|sufficient_evidence|2|2|0.03200850|
|vision-transfer|insufficient_evidence|1|1|0.03407025|
|contextual-followups|insufficient_evidence|2|2|0.06720810|
|unsupported-business-cost|invalid_decision_limit|2|2|0.03073575|

正文证据文档数统计初始研究中 search_documents 返回非空正文或 read_pages 返回页的不同 document_id；数量只说明实际范围，不表示引文语义支持或质量通过。连续追问两轮均 finished / sufficient_evidence，原始初始研究停止原因仍保留。

`vision-transfer` 仍只入库并取得一篇正文证据；`unsupported-business-cost` 两次 verify_citations 均返回 citation_validation_failed（Inline evidence IDs and citation list do not agree），随后 answer_validation_failed 累计触发 invalid_decision_limit；这是回答与引用列表不一致，非网络失败或未知费用，保留真实回答/失败与工具轨迹，作为后续待办。没有为这些结果改策略、补齐论文或重跑。`sufficient_evidence` 是 Agent 的停止原因，不能代替人工通过判定。

与原始 baseline-fixed-20261105-001 的逐例比较见 [comparison.md](comparison.md)；回答、用量、模型事件及工具摘要见 [candidate-fixed.json](candidate-fixed.json)，完整比较材料见 [comparison.json](comparison.json)。前后实际取得的论文子集仍有差异，比较器逐例保留条件差异；不能据此声称同材料质量提升。连续追问例还记录 actual_model_settings_differ：旧基线的一次未知调用 returned_model=null，新版已知调用均为 gpt-5.4-mini-2026-03-17；requested_model 相同，此缺失字段不能被填造或静默抹去。四例达到双文档证据范围，也不等于语义质量提升。

五例和两轮追问全部已执行，但人工评审为0；关键结论、物理页码、引用支持关系及至少4/5门槛尚未核查。[HUMAN_REVIEW.md](HUMAN_REVIEW.md) 与两个 reviews.template.json 提供逐轮待审入口，模板不是评审结论。所有 review verdict 保留 not_reviewed，分数为 null。

本轮固定材料命中准备阶段的真实缓存，未验证 Agent 在线搜索到 PDF 获取的完整链路。原 online 冒烟及其不足仍保留在旧 local-20261105 中，本轮不把缓存当在线成功。独立对话/停止/删除等行为仍需结合现有独立工程验证，五例执行不替代生命周期验收。

完整原始记录保留在实施工作树 `tmp/worktrees/agent-v1-issue-8/data/evaluations/candidate-fixed-20261105-002/` 及 `data/research_runs/`，工作树明确保留不清理。新本地可点击PDF物理页的评审包位于该工作树 `data/evaluations/review-packet-20261105-002/review.html`；已核对五篇PDF存在、SHA256及物理页数。PDF与原始任务不进入Git，旧记录及模板未覆盖。

初始只读预检因沙箱拒绝 loopback socket 而失败，未发生模型提交；经工具 require_escalated 允许本机接口和已授权模型网络后，新只读预检通过。五例后台 jobs 均 finished，所有 active_turn=false、busy=false 后停止服务；无遗留付费任务。

五例属于小样本，模型输出存在波动；即使未来达到4/5，也不代表总体准确率或统计显著提升。
