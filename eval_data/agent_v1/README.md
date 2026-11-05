# Agent V1：本地基线与五例验收

对应 Issue #2。以本地工作区为准，工具只采集现有 Web 接口，不改变 Agent 提示、模型配置、业务流程或预算来改善基线成绩。

## 本次实际状态

五例输入和人工评分规则已建立，官方 arXiv 版本页面的书目信息已核对。
最初基线预检记录缺少可用 API Key、对应 PDF、兼容固定来源报告和核对过的页级原文依据，当时网络策略未放行 OpenAI 和 arXiv。这是历史记录；后续环境是否就绪须重新预检。
截至 2026-11-05，已在保留的旧版源码上重新运行固定材料五例，并在旧版与集成版各运行一例在线冒烟；这是当前取得的真实模型记录，不是历史上业务改造前已执行的证明。人工来源语义核查与逐例评分仍待完成，**没有质量通过率，Issue #2 保持打开**。逐条验收状态与证据索引见 [Issue #2 状态](validation/local-20261105/issue-2-status.md)，公开脱敏实测见 [本机验收记录](validation/local-20261105/README.md)。`baseline/status.json` 保留最初阻塞的历史事实；业务改造前的原始本地文件指纹见 `baseline/workspace-before.json`。

随后已在修复后的宿主完成另一次固定材料五例与两轮追问，见 [修复后复测](validation/retest-20261105/README.md)。其输入、来源和评分规则哈希、模型配置及 $0.10/$0.05 请求预算与旧版一致；版本工作区指纹单独记录。原始结果不覆盖，人工仍待审，完整在线链路仍未证明，#1/#2/#8 不据此关闭。

已有旧的单篇缓存实测不能替代这五例。新增自动化测试只证明采集工具正确处理 HTTP 结果、连续追问、拒绝、缺失材料和结果未知等情况。

## 文件与输入

- `suite.json`：五例公开研究输入、日期、目标论文、追问与人工预期。比较工具使用、反馈机制、视觉论文方法、连续追问及业务成本证据缺口。
- 根目录 `sources.json`：初始固定版本的官方地址和书目信息；其未填写字段保留初始状态。2026-11-05 实跑实际使用的来源及 PDF 校验值、物理页数和 AI 辅助定位原文见 `validation/local-20261105/sources.json`，人工语义核查仍待完成；复核实跑须以记录中的冻结哈希为准。
- `rubric.md`：冻结前已定义的评分规则和硬性失败。
- 采集输出分离执行状态与人工评分。`executed` 不自动赋予通过分数，Key 格式就绪不代表凭证有效。

## 公开版本入口

当前交付集成分支为 `codex/agent-v1-delivery-20261105`，此前发布的 `origin/codex/agent-v1-integration` 保留用于追溯；改造前基线源码单独发布于 `origin/codex/agent-v1-baseline`。请在不同 checkout 中运行，以免服务与采集器混用代码或覆盖数据：

```bash
git fetch origin
git worktree add --detach ../paper-assistant-baseline origin/codex/agent-v1-baseline
```

本次公开基线提交为 `805c312c30ff320b5aca93c9221840c50f8cdb24`，tree 为 `67bdab05721b34b069874fdcae10d5d32cf20daf`，已核对与本地 `c4b26c8` 源码树一致。可在基线 checkout 用 `git rev-parse HEAD^{tree}` 核对。远端采用等源码树快照，提交 SHA 不等于本地历史提交号；保留 `baseline/workspace-before.json` 的原始 HEAD 和差异指纹作溯源。基线分支不包含后续新增的材料准备器和比较器；先在当前分支取得真实材料并人工核查，再完整复制 PDF 缓存（含 manifest）与同字节 seed 到基线 checkout，使用其原始 freeze/run 入口。评审和比较在当前分支读取两个独立结果目录。源码可复现不代表历史真实基线已执行，仍不能事后伪造改造前结果。

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

最初预检时材料完整性未满足；2026-11-05 已取得固定版本 PDF、校验信息与 AI 辅助页级定位，并执行旧版五例，人工语义核查仍未完成。不能据此宣称 Issue #2 全部完成，也不能把此次旧版重跑标为历史上的改造前执行。

## 真实模型采集

在项目配置自己的 API Key，并使用已放行对应域名的环境。以下命令会调用真实模型并计费：

```bash
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-001 --out data/evaluations/baseline-online-001 --mode online --execute
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-001 --out data/evaluations/baseline-fixed-001 --mode fixed --execute
```

可用 `--case contextual-followups` 单独运行一例。原基线接口的初始研究上限为 $0.10；V1 允许 $0.50，但可比复测保留冻结请求初始研究 $0.10、追问每轮 $0.05，不静默提高预算；全部五例初始研究加两轮追问，单模式配置额度合计 $0.60。它是应用估算额度，非账户硬限额。两种模式分别留结果，不能把两轮当作一次。

采集器在付费 POST 前保存检查点，研究启动不自动重试；提交响应丢失标为 `submission_unknown`。原基线应用的初始研究请求尚无幂等保障（V1 网页已持久化 request_id，但旧采集请求可能省略它），不能重新执行同一命令来“继续”未知任务。
使用保存的任务标识在原页面确认状态；研究在采集器超时后会收到一次停止请求，仍需等当前调用结束。**在确认后台状态前不要开新的真实采集来替代原任务。**

每个输出目录包含汇总 `report.json` 与逐例记录：实际输入、HTTP 状态、研究及追问快照、模型配置和指纹、工具轨迹、实际策略绑定、停止原因、token、估算费用和采集耗时。当前 Web 报告以外的工具详情只从该任务的已知本地记录补充读取，不开放任意文件 HTTP 接口。

这些原始记录只保存在已忽略的 `data/` 中。公开结果前人工检查脱敏、去掉本机路径及无关内容；提交必要来源与短证据，不自动上传 PDF 或原始运行目录。

## 人工评分与比较

按 `rubric.md` 逐例、逐轮填写结果中的 `review`，每条关键结论记录论文、原文、页码及支持关系。没有原文依据时保留 `not_reviewed`。
真实来源或模型调用未执行的条目为未执行；模拟 HTTP 测试结果不能进入真实模型结果表。五例均完成执行与评审后，才能判断是否达到 4/5。

改进版本使用同一冻结输入运行到另一个新目录，保留模型、材料和请求预算的差异说明。报告每例变化及已知局限，不预先承诺分数提升。

## 官方固定材料准备入口（不调用模型）

以下命令从项目根目录执行；输出目录必须不存在。此步骤只访问 arXiv，不需要模型 Key，也不消耗模型额度。网络仍需允许 `export.arxiv.org`（元数据）与 `arxiv.org`（PDF）；失败会写入 `report.json`，不会标为材料已就绪。

```bash
uv run python -m paper_assistant.material_preparation --out data/evaluations/materials-v1-001
```

准备器读取当前 `sources.json` 中冻结的版本，通过官方 API 的 `id_list` 一次请求精确版本，例如 `2210.03629v1`；保留未经改写的 `metadata.xml`、实际请求 URL 和响应 SHA。只有默认真实 HTTP transport 才标为 `live_arxiv_api`；可控测试 transport 明确标为 `injected_transport`，默认业务加载器拒绝它。返回版本集合、题名或首次提交日期与冻结记录不符时停止，不能把最新版本改写成 v1。若官方确实不提供所需版本，应记录阻塞；修订来源须另建明确的新冻结版本。

全部五篇构成同一个全局 seed，而不是为各案例按答案定制候选。它覆盖所有案例共有的日期窗口；仍由现有研究流程筛选论文。`seed.json` 使用现有 `load_search_candidates` 的完整报告格式，另外明确 `request_kind=exact_version_id_list` 与真实 API URL：这是固定版本材料准备，不是一次真实关键词发现的质量证明。JSON 中 `request` 的 query/日期供旧加载器建立研究范围，不冒称为实际关键词网络请求。

PDF 使用现有 `PaperFetcher` 下载到项目 `data/paper_cache`，保留它生成的真实来源、版本、重定向与文件校验 manifest；每篇单独采用现有有界获取会话，避免把五篇材料准备当成单次研究的三篇上限。失败不重试，重跑请使用新输出目录；真实缓存可复用。目录内还会生成：

- `sources.prepared.json`：实际 PDF 相对路径、SHA、物理页数，`evidence` 仍为空；不会覆盖原来源记录。
- `evidence.draft.json`：每页开头至多 350 字符的原文摘录，`claim`、`reviewer` 留空，全部为 `not_reviewed`。它只是定位起点，未做相关性或语义支持判断，可能包含标题、目录或无关文字。
- `report.json`：各篇真实获取结果及阻塞；无获取阻塞时也只能是 `pending_human_review`，不是基线执行成功或人工评分通过。

人工逐篇打开固定 PDF，结合 `suite.json` 的预期核对方法、实验条件与局限；需要时转到其他物理页重新选取短原文。把确认后的 `page_number`（从 1 开始）、`quote`、其支持的具体 `claim` 和真实 `reviewer` 填入 `sources.prepared.json` 的相应 `evidence`。不能把待审草稿批量改为已审，也不能仅凭文本匹配证明语义支持。此目录与来源证据均不进入模型请求。

完成评审后，在启动 Web 服务前安装同一份 seed 与来源记录：

```bash
# 如目标已有旧 seed，先备份它；不要覆盖仍在运行的研究所用输入。
mkdir -p data/literature_search/smoke
cp data/evaluations/materials-v1-001/seed.json data/literature_search/smoke/20261023.json
cp data/evaluations/materials-v1-001/sources.prepared.json eval_data/agent_v1/sources.json
uv run python -m paper_assistant.evaluation freeze --out data/evaluations/v1-inputs-002
uv run python -m paper_assistant.evaluation run --frozen data/evaluations/v1-inputs-002 --out data/evaluations/preflight-fixed-002 --mode fixed
```

`fixed_seed.sha256` 已由准备器根据输出 seed 的真实字节计算；复制时保持原始字节。`20261023.json` 是当前 Web 宿主的兼容路径名，不代表此次材料采集日期；真实时间记录在 API/PDF 元数据中。预检继续检查 seed、应用真实 PDF 缓存、页码、原文与人工填写字段，新采集应满足这些检查；本轮来源只有 AI 辅助定位、未取得人工签署的事实必须随实测保留，不能倒填为已审。模型 Key 与模型网络在下一步真实 Web 采集时单独检查，材料准备完成不代表它们就绪。

在业务改造前保留的基线工作树运行时，应从该工作树执行上述准备与冻结命令（或完整复制经过核验的 `data/paper_cache` 及同字节 seed，并保留相对路径）；不要只复制 PDF 而遗漏 manifest。不能在改造后的工作树执行后标为旧版本基线。

## Issue #8：导入人工评审与发布逐例比较

`paper_assistant.evaluation_review` 是记录校验器，不会自动判断语义支持，也不验证评审者身份。先由真实评审者打开回答、逐轮工具记录和固定 PDF 核查，再准备 JSON 文件；顶层是 case_id 到 review 的映射。以下只是字段模板，禁止直接把占位值当人工结论提交：

```json
{
  "tool-use-comparison": {
    "reviewer": "填写实际评审者",
    "reviewed_at": "填写带时区的 ISO 8601 时间",
    "verdict": "not_reviewed",
    "failure_reasons": [],
    "checks": {
      "task": "待核查",
      "agent_behavior": "待核查",
      "traceability": "待核查",
      "support": "待核查",
      "execution_boundaries": "待核查"
    },
    "unobserved_branches": ["填写未触发、未观察的分支；全部观察过才留空"],
    "claims": [
      {
        "turn_id": "research",
        "claim": "填写回答中的一条关键结论",
        "source_version": "填写实际论文版本标识",
        "page_number": 1,
        "quote": "填写该物理 PDF 页中的短原文",
        "support": "unsupported",
        "reason": "填写支持关系与实验条件核查理由"
      }
    ]
  }
}
```

完成核查后 verdict 填 `pass` 或 `fail`，五项 checks 均填 `pass` 或 `fail`；失败需列出 failure_reasons。claims 的 support 使用 `supports`、`partial`、`unsupported`；虚构或无关证据用 `fabricated_paper`、`fabricated_citation`、`fabricated_page`、`wrong_source`、`unrelated`，成本偷换用 `business_cost_substitution`，无依据穷尽声明用 `unsupported_exhaustiveness`。也可以在 `hard_failures` 数组明确记录硬性失败。硬性失败覆盖矛盾的 pass；普通 pass 与失败理由、部分支持或不支持冲突也判 fail。缺字段保持 pending。

初始研究 turn_id 固定为 `research`；每轮追问使用原始 snapshot.turns 中实际保存的 id，各轮均需核查，不能只评审最后一次回答。claims 应覆盖所有关键结论，工具只能校验记录完整性，覆盖范围及引文语义仍需人工负责。对于缺少正文、无法核对的结果，保留 not_reviewed，不为满足字段要求拼凑引文。

```bash
# 原始采集保持原样；分别导入前后版本各自的真实评审。
uv run python -m paper_assistant.evaluation_review review --candidate data/evaluations/baseline-fixed-001 --reviews data/evaluations/baseline-reviews.json --out data/evaluations/baseline-fixed-reviewed-001
uv run python -m paper_assistant.evaluation_review review --candidate data/evaluations/candidate-fixed-001 --reviews data/evaluations/candidate-reviews.json --out data/evaluations/candidate-fixed-reviewed-001
uv run python -m paper_assistant.evaluation_review compare --baseline data/evaluations/baseline-fixed-reviewed-001 --candidate data/evaluations/candidate-fixed-reviewed-001 --out data/evaluations/comparison-fixed-001
```

所有输出目录须不存在。review 输出派生 report.json 和逐例 review-validation.json；compare 输出 comparison.json 和 comparison.md，在线模式另存目录执行相同步骤。命令成功表示生成了记录，不表示通过验收，必须读取 status 与 reasons。输出保留人工记录、逐轮回答/材料/工具轨迹、缓存来源、停止原因、耗时和模型事件。费用只从模型事件按持久化轮次累计，同一追问在多个快照出现不重复收费；状态中的 usage 仅作为参考，未知调用的预留单列。

compare 对齐冻结五例，核对 suite/sources/rubric SHA、实际请求及预算、模型设置和取得论文版本/字节。即使前后都改成相同预算或漏掉相同追问，仍标记与冻结输入不符。request_id 作为幂等标识不算研究条件变化。策略绑定、提示及处理配置变化单独展示；策略改进本身不禁止同条件比较。缓存 origin 来自 live 也不表示本次联网；在线模式必须实际观察成功的非缓存搜索及 PDF 获取。

五例存在未执行、未评审、非真实模型记录、缺失轮次用量或条件差异时，整体保持 blocked，quality_pass_rate 为 null。完整且同条件时才给出五例中通过比例及至少 4/5 的结果。固定材料质量复测、在线链路验证与自动化生命周期/默认预算测试分别留证，不把 controlled_web_test 算入模型效果。

公开的历史阻塞样例位于 `validation/preflight-20261104/`：它记录当时只读预检，未提交真实模型请求，没有人工质量判定。`baseline-unavailable` 明确记录当时不存在原始真实基线，不是补造的运行；它不覆盖 2026-11-05 新取得的旧版实跑，当前状态见上述 Issue #2 状态页。可无 Key、无模型费用复现比较命令：

```bash
uv run python -m paper_assistant.evaluation_review compare --baseline eval_data/agent_v1/validation/preflight-20261104/baseline-unavailable --candidate eval_data/agent_v1/validation/preflight-20261104/fixed --out data/evaluations/blocked-example-001
```

公开前人工脱敏并提交必要记录。五例小样本与模型波动限制保留在比较报告中；没有原始真实基线时不能声称优化提升或 V1 已验收完成。
