# Issue #2：冻结基线与五例验收状态（2026-11-05）

对应 [Issue #2](https://github.com/peanut0611/paper_research_assistant/issues/2)。**保留打开：已有真实模型执行记录，人工来源语义核查及逐例结论支持度评分尚未完成，quality_pass_rate=null。** 本页只整理现有证据，没有重跑、修改冻结材料或评分规则，也没有改写原始实测。

## 时间与证据边界

- `../../baseline/status.json` 与 2026-11-04 Issue 评论记录当时的 Key、网络和材料阻塞，仍是有效历史记录。
- 2026-11-05 已在保留的旧版源码上实跑固定材料五例；集成版也实跑五例，两版各做一例在线冒烟。这是现在对旧版的重新执行，不能证明历史上业务改造前已执行。
- 公开脱敏原件为本目录 `baseline-fixed.json`、`baseline-online.json`、`candidate-fixed.json`、`candidate-online.json`、`sources.json`、`summary.json` 与 `comparison-status.json`。原始本地目录未被本次文档更新改动。
- 下文旧版根为 `tmp/worktrees/agent-v1-baseline/`，集成根为 `tmp/worktrees/agent-v1-integration/`，均相对原项目根；这些本地 `data/` 记录不随 Git 发布，不能把路径存在误当成公开材料已齐。

## 逐项验收

|AC|当前状态|证据与剩余工作|
|---|---|---|
|1. 本地 HEAD、差异和必要文件指纹|已留存|`../../baseline/workspace-before.json` 保存业务改造前本地 HEAD、差异及文件指纹；实际运行的 `workspace` 另存于四份实测 JSON 和各冻结目录。不是完整源码备份，不含密钥内容。|
|2. 固定五例研究输入与预期|已冻结|冻结 `suite.json` 包含五例 topic/questions、UTC 日期窗口、目标两篇及人工预期；覆盖自主筛选、补查、多论文、连续两轮追问、证据不足/失败，包含视觉领域，未把领域限定为 RAG。分支未观察不算已覆盖。|
|3. 真实来源、固定版本、材料校验及原文依据|完整性已留证；人工待审|本目录 `sources.json` 记录五篇官方 v1、PDF SHA/页数及短原文位置，状态为 `integrity_ready_human_semantic_review_pending`。AI 辅助定位不能替代真实评审者对原文与结论支持度的判断。|
|4. Web 研究/追问采集及模型、策略、用量等|固定五例已执行并留证；在线仅冒烟|旧版 `baseline-fixed.json` 五例均为 `executed`/`real_model_http`；连续追问有三轮记录。JSON 包含实际请求、策略绑定、工具轨迹、停止原因、token、耗时、估算费用。连接错误轮次有未知预留，不能补造缺失工具轨迹或精确用量。在线只跑 `tool-use-comparison`，其余四例未执行在线采集。|
|5. 保留实际失败、接口/预算差异|已记录|旧版五例初始停止均为 `insufficient_evidence`，反馈例无可显示回答；两版追问各有一次连接错误/未知用量。集成版另有 `openai_incomplete`、`invalid_decision_limit`。没有通过增加冻结预算或重放未知调用改善结果。|
|6. 预先固定人工支持度规则|规则已留存；人工评分未完成|`../../rubric.md` 及实跑冻结副本规定：虚构论文/引用/页码或无关引文支持关键结论导致该例失败；准确说明证据不足可通过。五例逐轮真实评审者、时间、关键结论及原文支持理由仍待填，不能把 `executed` 当 `pass`。|
|7. 重复操作与逐例评分方法|方法已提供；执行范围有明确边界|`../../README.md` 给出 freeze/preflight/run/review/compare 入口；人工评审包见下。已有一次固定五例真实采集；在线其余四例与完整搜索到 PDF 链路未验证，均不能据模拟测试补齐。|

## 可复核固定条件

旧版提交为 `805c312c30ff320b5aca93c9221840c50f8cdb24`，本地 Git 可验证其 tree 为 `67bdab05721b34b069874fdcae10d5d32cf20daf`。旧文档记载该 tree 与原本地 `c4b26c8` 一致；本次 checkout 无该短 SHA 对象，不将历史记载冒充新完成的对象核验。原始本地工作区指纹的 HEAD 为 `7c18c8b1c90b2f532536a6ee629fbf195d3c9308`，与公开等树提交的身份应分别保留。

旧版实际冻结目录为 `data/evaluations/inputs-20261105-001/`，冻结时间 `2026-11-04T15:24:09.599453+00:00`（UTC）。下表为运行记录中的 SHA-256，已逐字节核对该目录：

|文件|SHA-256|
|---|---|
|suite.json|`a1051032549b9fcb35005453fb9827768b6a8ea5af31d9c8ffb9ba36c0b3acb9`|
|sources.json|`ae529159149fbee15b14e0a75799dc469dc04e32f713ade0bde4f7ed821d49da`|
|rubric.md|`7120c6b78db3578d19b3cf4a780d02978c13c23816d9ce72a30726dcd42c515e`|
|workspace.json|`22c7bd5c24d0c8c4b4849ea1ecef270ec64e887c9a90e8cfa3564ad03c95ce9d`|

两版实测 suite/sources/rubric SHA 相同，workspace 指纹分别记录。不要用仓库当前未准备材料的 `sources.json` 或重新序列化的 JSON 替换运行冻结副本。

- 五篇版本：ReAct `2210.03629v1`（31 页）、Toolformer `2302.04761v1`（17 页）、Reflexion `2303.11366v1`（17 页）、ViT `2010.11929v1`（21 页）、CLIP `2103.00020v1`（48 页）。两工作树五篇 PDF 字节均与公开来源中的 SHA 一致。
- 同字节全局 seed 为 `data/literature_search/smoke/20261023.json`，SHA `1696bd44cdbedbcf1b331cc0644c35dfa008e128968985ef85c6fccf016db8c8`；两工作树均已核对。集成根 `data/evaluations/materials-20261105-002/report.json` 保存官方精确版本 `id_list` 请求，`metadata_origin=live_arxiv_api`、`model_calls=0`、`pending_human_review`。固定模式命中准备阶段缓存，不证明 Agent 当轮在线下载成功；可控测试响应不算真实效果证据。
- 五例统一 UTC 窗口为 `[2020-01-01T00:00:00+00:00, 2024-01-01T00:00:00+00:00)`，按 submitted 日期，起始包含、结束不包含。每例目标两篇，追问仅连续追问例两轮。
- 模型记录：`gpt-5.4-mini`，input 上限 16000、output 上限 1200、timeout 45 秒，`reasoning effort none`；实际策略/提示和执行限制见每轮 binding/model events。旧版原始 report 保存 `research_instructions_sha256=f300936fb12e54fa9374f78369c4281a67f7e3ce8812836224184309a29a56cc`。embedding 身份见本目录 `embedding-identity.json`，两版使用同一 multilingual-e5-small 权重。
- 冻结预算：初始研究 $0.10、每轮追问 $0.05，单模式五例额度合计 $0.60；两版固定五例及各一例在线冒烟配置总额度 $1.40。没有采用 V1 默认 $0.50/$0.10。用量/费用为应用记录及保守预留，非供应商账单。
- 旧版原始 `data/evaluations/baseline-fixed-20261105-001/report.json` 的 SHA 为 `c74ea460249a3724a3b5b321dc589586e9956affe03bfe6cd0f592ebc47a9b75`，与公开 `baseline-fixed.json.raw_report_sha256` 已核对。

## 待办与交接

1. 人工逐篇核查固定 PDF、原文页码及其支持的具体结论，签署真实来源评审；保留 AI 辅助定位与人工判断的区别。
2. 从集成根 `data/evaluations/review-packet-20261105-001/HUMAN_REVIEW.md` 或 `review.html` 查看两版逐例逐轮回答及工具轨迹，再填写独立的 `baseline-reviews.template.json`、`integration-reviews.template.json` 派生文件；空模板不是已审证据。按 README 的 review/compare 生成新目录，原始采集不改。
3. 记录所有关键结论支持关系、失败和未观察分支。现有比较 `blocked`，`vision-transfer` 与 `unsupported-business-cost` 实际取得论文子集不同；不能强行补齐材料后冒称原运行同条件，不能据本轮声称质量提升。
4. 在线两版只观察到真实搜索且无候选，未观察搜索成功后的 PDF 获取链路；其余在线四例为未执行。后续若需扩大验证，须单独授权/登记新的真实运行，不在此次文档任务重跑。
5. 已有记录中未知付费请求保持未知预留，不自动重放。人工评分完成且比较条件满足后才能讨论至少 4/5；目前人工已完成数为 0，#1/#2/#8 均不得据此关闭。

本次验证仅含文件存在、原始 report/冻结文件/两工作树 PDF 与 seed 哈希、Git tree、五例及追问轮次数和模型/预算记录交叉检查；未访问或展示 `.env`，未请求付费模型，未新增无行为意义的测试。
