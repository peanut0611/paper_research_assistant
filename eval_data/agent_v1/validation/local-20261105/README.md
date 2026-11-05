# 2026-11-05 本机真实模型验收记录

**状态：人工待审，质量门槛未证明达到，#1/#2/#8 保持打开。**

这是在本机重新运行旧版源码与集成源码取得的实测，不宣称历史上业务改造前已完成基线。
本轮没有提交、推送或关闭 Issue。原工作区的 17 个未提交文件已另存快照并保持原样。

|版本|固定材料案例|初始研究停止原因|有可显示回答|已入库全文|本例费用/预留 USD|
|---|---|---|---|---:|---:|
|baseline-fixed|tool-use-comparison|insufficient_evidence|True|1|0.03388725|
|baseline-fixed|feedback-and-evidence|insufficient_evidence|False|1|0.02460690|
|baseline-fixed|vision-transfer|insufficient_evidence|True|1|0.02108340|
|baseline-fixed|contextual-followups|insufficient_evidence|True|1|0.05936865|
|baseline-fixed|unsupported-business-cost|insufficient_evidence|True|2|0.02421465|
|candidate-fixed|tool-use-comparison|openai_incomplete|False|1|0.04126425|
|candidate-fixed|feedback-and-evidence|insufficient_evidence|True|1|0.02238750|
|candidate-fixed|vision-transfer|insufficient_evidence|True|2|0.03950925|
|candidate-fixed|contextual-followups|insufficient_evidence|True|1|0.05133225|
|candidate-fixed|unsupported-business-cost|invalid_decision_limit|False|1|0.03421500|

两版固定材料五例＋两版各一例联网冒烟，总计 token（含未知预留）362260，费用/保守预留 **$0.36598185**，其中未知预留 **$0.03001500**。这不是供应商账单核对。

## 比较条件与证据范围

- 固定请求初始研究 $0.10、追问每轮 $0.05；未使用 V1 默认的 $0.50/$0.10 改善成绩。
- 相同五例、评分规则、固定版本 PDF/哈希及同字节全局 seed；同一 GPT-5.4-mini 配置和同一 multilingual-e5-small 权重。各轮实际策略与模型设置保存在 JSON。
- 冻结候选池相同，但 `vision-transfer` 和 `unsupported-business-cost` 两例实际取得的论文子集不同；原比较器因此记录 `actual_paper_versions_or_bytes_differ` 并保持 blocked。没有为获得可比标记强行补齐或删减论文，不能据本轮声称同材料质量提升。
- 不把目标篇数当质量分数。只有取得并检索到支持证据，才能完成相应比较；仅入库还不等于读到关键内容。
- 原始回答、各轮工具和模型事件、耗时、停止原因和费用保存在四份脱敏 JSON；完整原始采集保存在各隔离工作树 data/evaluations/，未覆盖或重放。
- 固定材料记录命中准备阶段的真实缓存；官方材料准备实际下载五篇 PDF，但不能把这当作 Agent 在线自主发现链路成功。
- 在线各执行一例：两版都实际访问 arXiv 搜索；本轮查询未取得候选，因此未验证 Agent 搜索到 PDF 获取的完整链路。新版第二次搜索命中本次查询缓存，如实保留。
- 两版连续追问各有一次连接错误/未知用量，保守预留，不重放。
- 人工评分、关键结论支持度及至少 4/5 的门槛均未完成；quality_pass_rate=null，不能将 executed 写成 pass。用户已选择暂保留人工待审。

## 本轮暴露的后续修复方向

1. 多论文任务过早进入收尾：有相关候选时仍只入库一篇；视觉例虽入库两篇，却没有取得 CLIP 的具体证据。需逐问题/逐论文证据覆盖和有界补查，不能只检查入库数量。
2. JSON 决策输出达到当前 1200 output token 上限而 incomplete：先缩短单次回答/引文与修复协议；本轮不提高冻结生成配置或预算、不补跑失败。
3. 搜索无结果时只调整词序，未形成有效的查询放宽：应验证面向多篇论文的查询拆分/改写及无进展反馈。
4. 引文字面匹配仍不能验证结论支持：原文谈人的能力、实验片段或个别设定时，不能自动推广为方法普遍能力。必须按评审包逐结论核对。
5. 追问网络故障的未知用量路径正常保留；下一轮增加可观察诊断，不自动重试未知请求。

## 人工评审入口

完整中文评审包与两个空白评审模板位于集成工作树 `data/evaluations/review-packet-20261105-001/`。模板不代表已评审，AI 只做材料定位和初步整理。
人工逐轮填入真实评审者、时间、结论、原文位置和支持理由后，再运行 evaluation_review review/compare。无回答或未核对部分维持待审，不拼凑引文。
五例是小样本，模型输出有波动；即使未来达到 4/5，也不表示普遍准确率或统计显著提升。

## 本地整合与工程验证

本地集成分支：`codex/agent-v1-local-validation-20261105`，位于原项目的 `tmp/worktrees/agent-v1-integration/`。原 `main` 工作区与其 17 个未提交文件保持原样，备份在 `tmp/handoff-20261105/`。当前改动和结果均未推送。

完整回归：601 passed、2 skipped（Windows 符号链接权限）；前端 32 passed。另有源码快照 XML 原始字节和材料准备定向回归 55 项通过。已修复评测工具的 UTF-8 默认读取、保留哈希快照原始字节，并将平台符号链接能力检查与路径穿越检查分开。未修改真实实测所使用的 Agent 策略或冻结预算。

Chrome 桌面 1440 与手机 390 宽检查无横向溢出；工作台新建草稿不提交模型，V1 对话切换与确认删除入口保留，旧重置入口已移除。四个临时验证服务均已停止，所有模型任务终态与原始记录已保存。
