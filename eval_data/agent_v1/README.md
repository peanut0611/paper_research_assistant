# Agent V1：本地基线与五例验收

对应 Issue #2。以本地工作区为准，工具只采集现有 Web 接口，不改变 Agent 提示、模型配置、业务流程或预算来改善基线成绩。

## 本次实际状态

五例输入和人工评分规则已建立，官方 arXiv 版本页面的书目信息已核对。
当前工作区没有可用 API Key、对应论文 PDF、兼容的固定来源报告或核对过的页级原文依据；环境网络策略未放行 OpenAI 和 arXiv。
因此**尚未形成真实模型基线，也没有质量通过率**。状态记录见 `baseline/status.json`；业务改造前的原始本地文件指纹见 `baseline/workspace-before.json`。

已有旧的单篇缓存实测不能替代这五例。新增自动化测试只证明采集工具正确处理 HTTP 结果、连续追问、拒绝、缺失材料和结果未知等情况。

## 文件与输入

- `suite.json`：五例公开研究输入、日期、目标论文、追问与人工预期。比较工具使用、反馈机制、视觉论文方法、连续追问及业务成本证据缺口。
- `sources.json`：固定版本的官方地址和书目信息；PDF 校验值、物理页数及人工原文证据当前为空，表示未完成，不能伪填。
- `rubric.md`：冻结前已定义的评分规则和硬性失败。
- 采集输出分离执行状态与人工评分。`executed` 不自动赋予通过分数，Key 格式就绪不代表凭证有效。

## 冻结本地版本

先在项目根目录同步依赖。以下命令同样适用于 Windows PowerShell；有 uv 时建议使用 `uv run`，或换成环境中的 Python 并保证能导入本地包。

```bash
uv sync --locked
uv run python -m paper_assistant.evaluation freeze --out data/evaluations/v1-inputs-001
```

冻结输出包含案例、来源记录、评分规则及本地工作区指纹，并为它们记录 SHA-256。
本地 HEAD、已跟踪差异的校验值、必要未跟踪文件及源码文件校验值均被记录；不读取密钥内容、不包含虚拟环境、模型/论文缓存及用户运行数据。
这是一份可区分本地差异的指纹，不是完整源码备份。提交业务改造前应保留对应本地源码；必要时另做安全备份。

输出目录必须是新目录，已存在时拒绝覆盖。修改输入或评分规则后应创建新冻结版本，并清楚标明基线是否仍然可比。

## 只检查，不发起模型调用

另开终端，从同一个本地项目启动 Web 服务：

```bash
uv run python -m paper_assistant web --local-models-only
```

采集器只接受本机 HTTP 地址。服务和采集器必须指向同一项目；`--local-models-only` 限制 embedding 权重加载，真实研究仍需要 OpenAI 网络。

```bash
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-001 --out data/evaluations/preflight-online-001 --mode online
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-001 --out data/evaluations/preflight-fixed-001 --mode fixed
```

未带 `--execute` 只读就绪状态和本地材料，不提交研究或追问。缺少服务、Key、网络权限及材料的原因分别记录；不会自动登录、下载或扩大网络权限。

## 补齐固定材料后再运行

1. 使用官方 arXiv 元数据及固定版本 PDF 取得真实材料。书目信息核验来源为各论文官方版本页面，而非候选摘要推断的正文事实。
2. 按现有发现和获取工具生成实际来源报告与下载缓存。当前 Web 固定模式只接受它自身使用的兼容来源报告，采集器不会把人工组装的报告标为 `live_arxiv_api`，也不会更换种子或注入候选。
3. 将实际使用的来源报告 SHA-256 记录到 `fixed_seed.sha256`。报告需覆盖案例中的固定论文版本，提交日期全部落入案例窗口。若真实报告使用其他版本，应在业务改造前明确修订案例和来源版本，重新冻结，不静默替换。
4. 为每个来源填写项目相对的 `pdf_path`、`pdf_sha256`、`page_count`，并添加 `evidence`：每项包含 `page_number`、短原文 `quote`、它支持的 `claim` 与核对者 `reviewer`。物理页码从 1 开始；不把参考依据传给模型。
5. 固定材料检查会核验 PDF 字节校验值、应用下载缓存的真实来源与相同文件校验值、页数，以及页内原文匹配。它不能替代人工判断语义支持度。
6. 重新冻结完整材料版本。预检无相关阻塞后，再执行下面的真实采集。

目前材料完整性未满足，不能据此宣称 Issue #2 的真实基线已完成，也不能改造后补跑并将其标为改造前基线。

## 真实模型采集

在项目配置自己的 API Key，并使用已放行对应域名的环境。以下命令会调用真实模型并计费：

```bash
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-001 --out data/evaluations/baseline-online-001 --mode online --execute
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-001 --out data/evaluations/baseline-fixed-001 --mode fixed --execute
```

可用 `--case contextual-followups` 单独运行一例。当前冻结请求初始研究 $0.10，追问每轮 $0.05；全部五例初始研究加两轮追问，单模式配置额度合计 $0.60。它是应用估算额度，非账户硬限额。两种模式分别留结果，不能把两轮当作一次。

采集器在付费 POST 前保存检查点，研究启动不自动重试；提交响应丢失标为 `submission_unknown`。原应用的初始研究请求尚无幂等保障，不能重新执行同一命令来“继续”未知任务。
使用保存的任务标识在原页面确认状态；研究在采集器超时后会收到一次停止请求，仍需等当前调用结束。**在确认后台状态前不要开新的真实采集来替代原任务。**

每个输出目录包含汇总 `report.json` 与逐例记录：实际输入、HTTP 状态、研究及追问快照、模型配置和指纹、工具轨迹、实际策略绑定、停止原因、token、估算费用和采集耗时。当前 Web 报告以外的工具详情只从该任务的已知本地记录补充读取，不开放任意文件 HTTP 接口。

这些原始记录只保存在已忽略的 `data/` 中。公开结果前人工检查脱敏、去掉本机路径及无关内容；提交必要来源与短证据，不自动上传 PDF 或原始运行目录。

## 人工评分与比较

按 `rubric.md` 逐例、逐轮填写结果中的 `review`，每条关键结论记录论文、原文、页码及支持关系。没有原文依据时保留 `not_reviewed`。
真实来源或模型调用未执行的条目为未执行；模拟 HTTP 测试结果不能进入真实模型结果表。五例均完成执行与评审后，才能判断是否达到 4/5。

改进版本使用同一冻结输入运行到另一个新目录，保留模型、材料和请求预算的差异说明。报告每例变化及已知局限，不预先承诺分数提升。
