# 文献研究 Agent

本地中文研究应用：输入研究领域、研究问题及日期范围，让单个 Agent 自主发现 arXiv 论文、筛选固定版本、获取正文、检索与读页、补查证据并形成研究回答。目标为 2–3 篇；材料不足时如实说明范围与缺口。支持独立研究对话、连续追问、引用原文与 PDF 页链接、执行记录、停止及确认删除。

这是受控 Agent 工程项目。模型选择动作并观察工具反馈；宿主负责范围、预算、时限、有限纠正及收尾。RAG 提供论文正文检索能力。**已保存两版五例真实模型记录，人工结论支持度仍待审；当前没有质量通过率或有效的前后提升结论。** 自动化行为测试、历史单篇实测、真实执行与人工验收分别记录，不能相互替代。

修复后的五例与两轮追问已执行，结果及失败见 [最新真实复测](eval_data/agent_v1/validation/retest-20261105/README.md)。人工评分与完整在线搜索至 PDF 链路仍待验收。

## 从干净环境启动

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Git。Node.js 20+ 用于前端自动测试，应用运行不需要 Node。以下命令在 Bash 执行；PowerShell 可用 `Copy-Item .env.example .env` 代替 `cp`。

```bash
git clone https://github.com/peanut0611/paper_research_assistant.git
cd paper_research_assistant
git switch codex/agent-v1-delivery-20261105
uv sync --locked
cp .env.example .env
```

本次交付使用 `codex/agent-v1-delivery-20261105` 集成分支；上述命令按该分支复现。之前的 `codex/agent-v1-integration` 和基线分支保留用于追溯。

在 `.env` 中填写自己的 `OPENAI_API_KEY`，不要提交或分享密钥。已有同名环境变量优先于 `.env`；空环境变量也会覆盖文件。配置只做本地格式检查，不证明凭证有效。包依赖由 `uv.lock` 锁定；首次安装需要下载依赖。

```bash
uv run python -m paper_assistant --help
uv run python -m paper_assistant web
```

打开 **http://127.0.0.1:8501**。服务默认只绑定本机，同一时间只执行一个任务。只打开页面、读取状态、切换或刷新不会请求模型。点击研究或提交追问会请求真实 OpenAI 模型并计费；API Key 对应的账户需自行启用 API 用量。

首次正文入库会通过 Hugging Face 下载 `intfloat/multilingual-e5-small`（包括 tokenizer 与权重）。需要允许 Hugging Face 及其实际下载重定向域名；在线研究还需要 arXiv 元数据/PDF 和 OpenAI 网络。`uv.lock` 不锁远端权重；实际工作区记录 embedding revision、tokenizer 校验值、维数及前缀，复现时应对照这些身份记录。已有完整权重缓存时可以使用：

```bash
uv run python -m paper_assistant web --local-models-only
```

这只禁止 embedding 资源下载；新机器没有缓存时无法入库，不是全离线研究模式。`--offline-sources`（研究 CLI）也只限制文献来源，模型仍联网计费。无权重、PDF 或 Key 时页面可以打开，但不代表研究已可执行。

## 完成一次研究与追问

1. 点击“新建研究对话”，填写领域、问题和日期，选择在线来源及目标 2–3 篇后开始。日期按 UTC，起日包含、截止日不包含；领域不限制为 RAG 或 Agent。首次可使用 `eval_data/agent_v1/suite.json` 的公开案例输入，但它们仍需真实材料与人工验收。
2. 查看候选、筛选及正文取得状态和执行记录。动作摘要与工具反馈显示实际调用及结果；下载成功还需成功入库才能检索。论文不足、下载/解析失败或无充分证据时保留实际范围与停止原因，不补造论文。
3. 展开回答下方的引用，核对论文固定版本、逐字原文和物理页码；“PDF 原文”链接指向该版本的 `#page=`。多页证据应逐页核查，PDF 阅读器对页锚点的支持可能不同。文本匹配只证明可追溯，**结论是否受原文支持仍需人工判断**。
4. 初始研究结束且至少一篇正文入库后，在“继续讨论”中追问，例如“上述方法各有哪些局限，实验条件是否可比？”。追问理解前文指代，重新检索或读页并校验引用。历史回答不是正文证据；追问不发现或添加新论文，需要扩大材料范围时新建研究对话。
5. 通过顶部列表切换已有研究对话。每个新研究对话分别保存问题、论文集合、历史、轮次与费用，检索和引用范围绑定当前对话；公共 PDF/模型缓存复用不授予跨对话引用权限。
6. 执行中可点击停止。停止会阻止后续调用，当前请求可能仍需等待返回。保留已完成材料、回答和执行状态；停止、失败或无证据不自动等于研究成功。
7. 删除时阅读并再次确认范围：永久删除该研究对话的问题、全部追问、专属索引、回答和执行记录；保留其他对话及公共缓存。运行中先停止并等待真正结束，不能仅凭点击停止就删除。

当前不提供重置功能。旧版本共享论文的追问线程和历史归档仍可读取，但不自动获得新版本独立研究对话语义。只读追问允许部分处理指纹变更，仍检查任务绑定、索引格式、PDF 哈希和 embedding 身份；继续写入旧工作区要求兼容指纹。不兼容时使用新研究对话，勿修改历史记录绕过检查。

## 执行限制与失败处理

| 范围 | 默认及上限 |
| --- | --- |
| 初始研究 | $0.50，5 分钟 |
| 单轮追问 | $0.10，2 分钟 |
| 当前研究对话追问累计 | $0.50，最多 20 轮 |
| 初始研究 + 追问 | 合计估算上限 $1.00 |

页面不提供预算编辑。显式较低的兼容请求仍按原额度执行。费用按记录的 token 和费率估算，未知结果保守预留整轮额度；已确认估算与未知预留应分开看，不要把模型事件与汇总重复相加。这不是账户硬限额，账单和应用估算可能有差异。达到费用、时间、步骤或工具限制后不再启动后续调用。

网页保存请求标识，刷新和重连只读，重复提交同一标识及内容不重复执行。初始 POST 支持 16–80 位字母、数字、下划线、连字符的 `request_id`；内容冲突返回 409。旧客户端省略标识仍兼容，但首次请求没有幂等保障。删除后仅留下任务标识和请求标识哈希 tombstone，拒绝过期请求重放，不保留问题或回答。

结果未知或服务重启后不自动重试模型。请求超时不代表模型未收到请求，不要新建任务来盲目重试。页面和 `GET /api/research/jobs/{task_id}` 可读执行状态；追问状态见同路径 `/conversation`。初始 job 应为 `finished` 或 `failed`，追问也须已终止。仍为 `stopping` 时继续等待。服务重启后无法确认后台执行已经结束的记录删除返回 409；检查终端和该任务 `web_process.log`，等待存活进程写回终态再刷新。崩溃遗留未知状态时保留记录并排查，不手改状态强行删除或重放。

## Agent 如何使用反馈

```text
研究问题 → 模型选择动作 → 搜索与筛选 → 固定版本 PDF → 解析入库
         ↖ 工具结果/失败反馈 ← 检索、读页、补查 → 引用校验 → 回答与缺口
                       宿主持续检查范围、预算、时限和停止条件
```

通用执行内核在 `src/paper_assistant/agent/`，研究宿主在 [research/agent.py](src/paper_assistant/research/agent.py)。GPT-5.4-mini 通过结构化动作选择工具；Dense、BM25、Hybrid 是正文检索选项。模型根据候选、PDF 获取结果及证据决定下一步，不要求展示隐藏推理。

重复已成功搜索会被拦截并给予有限纠正机会；可以改写关键词或调整筛选。首次检索后若未读页或改写查询就认定证据不足，预算允许时会提示补查。已有证据却持续无进展、接近步骤/工具/模型额度时进入收尾，只允许提交引用校验及回答；校验失败反馈具体证据和原文供有限修正。补查、修正和收尾不扩大预算，也不保证取得充分材料。工具轨迹、公共执行事件、模型事件和停止原因用于复核实际行为，动作目的摘要本身不证明工具已经执行。

## 验收、复现与证据边界

[五例材料、冻结、在线/固定采集与人工评分](eval_data/agent_v1/README.md) 包含完整命令。材料准备器只访问官方 arXiv，不调用模型；采集器默认预检，只有 `--execute` 才提交付费任务。在线模式须核对实际请求和缓存命中，不能把 `from_cache=true` 的结果当成本次联网成功。固定模式使用真实固定版本 PDF、来源 manifest 和 seed；精确 ID 材料准备不能证明关键词发现质量。模拟 transport、合成 PDF、模拟模型及文本匹配均不能充当真实效果证据。

真实模型执行后还要按 [rubric](eval_data/agent_v1/rubric.md) 人工逐条核对结论、实验条件、页原文和支持关系，再使用 `paper_assistant.evaluation_review` 的 review/compare 命令生成对照。基线复现使用独立的 `origin/codex/agent-v1-baseline` 分支（在单独 checkout 中运行）；公开发布可能是等源码树快照，应核对发布时记录的 tree 身份，不能假定本地历史提交号在远端可用。本地原始 HEAD 与差异指纹仍用于溯源。

当前 [基线状态](eval_data/agent_v1/baseline/status.json) 仍为阻塞；不能在改造后补跑并冒称改造前基线，也不能据 `executed` 声称 4/5 通过。历史 [单篇实测](eval_data/research/E2E_VALIDATION_20261028.md) 和 [追问人工步骤](eval_data/research/CONVERSATION_TESTS.md) 仅代表各自范围。

[交付核验记录](eval_data/agent_v1/DELIVERY_VERIFICATION.md) 记录隔离安装、命令与只读页面检查及未完成项。五例真实网络/模型/人工评审仍待完成；小样本单次差异也不能解释为普遍准确率或统计显著提升。

## 开发验证与目录

```bash
uv run pytest -q
node --test tests/web/research_client.test.cjs tests/web/conversation_client.test.cjs
```

自动测试使用模拟模型、合成 PDF 和固定快照，不需要真实 Key、权重或论文，不调用付费模型。CI 同样不配置凭证，执行 Python 与前端行为回归；它不验收真实模型效果。

- `src/paper_assistant/`：Agent、研究宿主、工具、Web 页面与 API、配置及统一入口。
- `tests/`：行为回归和注明来源的 fixtures；`scripts/`：兼容入口及开发工具。
- `eval_data/agent_v1/`：五例输入、来源元数据、rubric、基线状态及交付记录。
- `GLOSSARY.md`：领域词汇；`AGENTS.md`、`docs/agents/`：协作约定。
- `.env.example`、`pyproject.toml`、`uv.lock`：配置模板与锁定依赖。

运行数据在 `data/models/`、`data/literature_search/`、`data/paper_cache/`、`data/research_runs/`。不提交密钥、PDF、模型权重、完整私有运行目录或虚拟环境。公开验收记录放在受跟踪的 `eval_data/`，先人工检查脱敏、本机路径和无关内容；不要误把被忽略的 `data/` 当作已交付材料。旧本地资料问答与默认索引已退役，无需准备 `paper/`，旧忽略归档不参与运行。

[命令与开发入口](scripts/README.md) · [测试材料说明](eval_data/README.md)

## 本机集成验证

保留本地工作台布局与 A1 离线检查工具，独立研究对话的新建、切换、删除以 V1 实现为准，取消旧追问重置按钮。运行 `python scripts/check.py` 查看工程检查；真实五例使用冻结预算，人工评审状态单独记录。
