# 当前版本手工验收用例

更新：2026-10-24。机器可读输入和核对依据见 [manual_cases.json](manual_cases.json)。
这里的“当前版本”指适配目录重构后的 Agent；前四题使用固定历史论文，第五题单独测试近期联网发现。
这些是待运行的验收输入，不是已经通过的 Agent 质量评测结果。

## 测试前准备

- 在项目根目录运行。API Key 填入根目录 `.env` 的 `OPENAI_API_KEY`，已有环境变量优先；不写入用例或命令文件。
- 前四题选择页面的 **缓存示例**，起始日 **2023-01-01**，截止日 **2024-01-01**，
  目标 **1 篇**，预算 **$0.10**，主题统一为 **RAGTruth hallucination corpus**。
- 截止日不包含在窗口内。RAGTruth 的 arXiv 编号虽然是 `2401.00396`，本机真实候选记录的
  首次提交时间是 `2023-12-31T04:43:45Z`，应使用上述历史窗口，不根据编号推算发布日期。
- 本机需要已有 `data/literature_search/smoke/20261023.json`、RAGTruth v2 PDF 缓存及 E5 模型。
  当前生成用例时已经核对种子候选、PDF 哈希及参考引文；这些本地材料不随 Git 分发。
  在另一台机器复现时，先按根 README 的搜索、获取、模型准备步骤建立缓存。
- 每题点击一次“开始研究”会创建新任务。命令行也必须使用新的 `--task-id`，不要恢复旧任务。
- 新界面的来源模式与预算在“更多设置”中。已有任务会默认展示结果，可展开“研究设置”修改表单；
  候选在“参考论文”中，计数、费用、任务 ID 和工具轨迹在“执行详情”中。
- 优先运行第 1 题；通过后再运行第 2、3 题，避免在同一个衔接问题上连续消耗预算。

启动页面：

```powershell
python -m uv run python -m paper_assistant web --local-models-only
```

## 1. 中文单事实基线：ragtruth-cn-basic

复制到“研究问题”：

> RAGTruth 语料库大约包含多少条由语言模型生成的回答？请根据论文正文回答，并给出页码和原文引文。

人工核对：近 **18,000 条回答**。PDF 物理第 **1 页**有直接依据。
不是 18,000 篇论文、不是 18,000 个用户。
完整原文、字符范围及源文件 SHA-256 保存在 JSON 中，仅供核对，不传入模型。

应观察到：选择 RAGTruth → 获取 PDF → 成功入库 → 检索或读页取得证据 →
校验引用 → 返回证据回答。这里描述前置依赖，不要求工具顺序完全固定，也不预先给模型写死行动。

## 2. 英文单事实对照：ragtruth-en-basic

其余表单字段与第 1 题相同，研究问题改为：

> Approximately how many LLM-generated responses does RAGTruth contain? Answer in English using the paper text, with a PDF page number and a supporting quotation.

人工核对：同样是 nearly 18,000 responses，要求英文回答。与中文题分别新建任务，
可用于初步观察语言差异；两题不能据此得出中英文整体质量相同或不同的结论。

## 3. 多证据与可能的查询调整：ragtruth-multi-evidence

研究问题：

> RAGTruth 的回答生成覆盖哪三类任务？人工标注在什么粒度上进行？请分别给出论文页码和原文引文。

人工核对：

- 三类任务是 **问答、数据到文本写作、新闻摘要**，物理第 **3 页**有依据。
- 人工标注包括 **样本级、词级**，物理第 **1 页**有依据。
- 每项事实分别核对其引用，不要求必须生成某个固定的 E 编号。

若第一轮只取得部分事实，观察模型能否换查询或读页补证据。
如果首轮已经取得全部依据，也可以通过；只有实际发生且有明确目的的调整，才能记为“查询调整演示”。
这条真实任务不能保证必然触发调整分支，确定性分支仍由现有模拟测试覆盖。

## 4. 证据不足：ragtruth-insufficient

研究问题：

> 仅根据 RAGTruth v2，这篇论文是否报告了该数据集在企业生产环境中的每月维护成本（美元）？如有，请给出金额和核算方法；缺少依据时请明确说明。

人工核对：应根据实际取得的材料说明缺少该业务指标，不编造金额或公式，
不把论文中的标注费用、模型调用费用解释成企业每月维护成本。
不要求用一段引文证明整篇论文“绝对没有”某信息；有限检索应诚实表述为未在已取得材料中找到。

此题以诚实的 `insufficient_evidence` 结束为目标，不能因为“没有答案”就判失败。

## 5. 近期联网发现：recent-online

此题改用 **联网发现**，不能继续选择固定 RAGTruth 缓存示例。

| 字段 | 输入 |
| --- | --- |
| 主题 | 检索增强生成中的幻觉检测 |
| 起始日（UTC，含） | 2026-09-01 |
| 截止日（UTC，不含） | 2026-10-24 |
| 目标篇数 | 1 |
| 预算 | $0.10 |

研究问题：

> 在指定日期范围内查找一篇与 RAG 幻觉检测相关的论文，说明它研究的问题和一个主要方法，并引用实际取得的正文证据。

没有预先指定论文或标准答案，也没有为生成此用例联网验证该窗口内的候选。
检查首次提交日期、主题相关性、选择理由、实际取得的 PDF 版本及正文引用。
网络失败、零候选或全文不可用时，应诚实记录来源受限；这不算完成了研究闭环。
这是固定日期窗口，不会在下次运行时自动变成“最近一个月”。

## 命令行：从 JSON 选择一题运行

下面在现有 `python -m paper_assistant research` 上运行，不读取参考答案。修改第一行的用例 ID 即可换题。
**执行这段命令会调用真实 OpenAI 模型。** 每题配置预算 $0.10，其中 $0.02 为后续报告预留；
应用估算和预留不等于账户硬限额。不要未经检查一次运行全部五题。

```powershell
$selectedCaseId = 'ragtruth-cn-basic'
$manualSuite = Get-Content -LiteralPath 'eval_data/research/manual_cases.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$matchedCases = @($manualSuite.cases | Where-Object { $_.id -eq $selectedCaseId })
if ($matchedCases.Count -ne 1) { throw '用例 ID 不存在或重复。' }
$manualCase = $matchedCases[0]
$researchTaskId = 'manual-' + $selectedCaseId + '-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$runArguments = @(
    '-m', 'uv', 'run', 'python', '-m', 'paper_assistant', 'research',
    '--task-id', $researchTaskId,
    '--topic', $manualCase.task.topic,
    '--start', $manualCase.task.start_at,
    '--end', $manualCase.task.end_at,
    '--target-papers', [string]$manualCase.task.target_papers,
    '--max-cost-usd', '0.10', '--report-reserve-usd', '0.02',
    '--max-steps', [string]$manualCase.execution.max_steps,
    '--max-model-tokens', [string]$manualCase.execution.max_model_tokens,
    '--max-output-tokens', [string]$manualCase.execution.max_output_tokens
)
foreach ($researchQuestion in $manualCase.task.research_questions) {
    $runArguments += @('--question', $researchQuestion)
}
if ($manualCase.execution.seed_search_results) {
    if (-not (Test-Path -LiteralPath $manualCase.execution.seed_search_results)) {
        throw '缺少本地候选种子，请先按 README 准备缓存。'
    }
    $runArguments += @('--seed-search-results', $manualCase.execution.seed_search_results)
}
if ($manualCase.execution.offline_sources) { $runArguments += '--offline-sources' }
if ($manualCase.execution.local_models_only) { $runArguments += '--local-models-only' }
Write-Output ('本次任务 ID：' + $researchTaskId)
& python @runArguments
```

`--offline-sources` 只约束 arXiv/PDF，OpenAI 仍联网计费。
`--local-models-only` 只让 Embedding 使用本地模型，首次未缓存时需先准备模型。

## 如何判定结果

在 `data/research_runs/<task-id>/` 检查 `result.json`、`runner_state.json`、`tool_trace.json`。
页面也能展示候选、回答、引用、工具轨迹及停止原因。

- **通过**：符合该题的 `expected.checks`，且人工核对事实和引文成立。
- **执行失败**：例如下载后未入库就检索/读页，连续参数错误、`no_progress` 或模型/API 错误。
- **预算/时限受限**：保留部分结果，单独记录；不能计作可回答题成功。
- **来源受限**：在线题网络失败、零候选或无可用全文；记录原因，不汇报研究质量分数。
- **未测试**：尚未实际执行，不能用静态校验或参考引文存在替代真实 Agent 通过。

已知失败轨迹 `web-20261024T092731-7a62fb1259a6` 在下载后跳过了 `ingest_paper`，
后续检索/读页连续失败，最后 `no_progress`。新用例没有修改 Agent 来绕过该问题；
如果基础题仍出现同类轨迹，应修正工具前置状态与模型决策衔接。

2026-10-24 更新：近期联网题另一次真实运行 `web-20261024T101106-52ef04b7e574`
成功搜索并下载后，在调用入库前被本地 `model_context_too_large` 检查停止。
已修复候选/历史/证据的逐级压缩，并增加未入库工具错误的恢复提示。
同一保存状态的本地预检已通过；缓存 PDF 的真实入库、检索、读页也在独立临时任务中验证通过。
这些检查没有调用真实 OpenAI，也不代表该在线题的模型决策已验收成功。
测试前请先停止旧页面服务、重新启动并刷新页面，再创建全新任务；原下载缓存可复用。

结束流程更新：若模型尚未校验回答就选择 `finish(sufficient_evidence)`，
现在会先返回纠正提示，默认允许一次补做 `verify_citations` 的机会，仍计入原预算。
重复提前结束仍以 `unverified_completion` 停止。测试时请检查轨迹中确实有成功的回答/引文校验，
不能仅根据模型最后写了“接下来验证”就判定成功。该恢复路径已用注入 SDK 响应验证，真实模型仍需重测。

目前只验收短证据回答，正式研究报告仍未实现。原文引文规则校验也不等于结论语义完全正确。
