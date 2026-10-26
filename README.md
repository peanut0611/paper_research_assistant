# 文献研究 Agent

根据主题和日期范围发现 arXiv 论文，选择并获取固定版本 PDF，将全文写入当前任务的知识库，再检索、读页和核验引文，生成带证据的短回答。

项目已收敛为 Agent 原型。原本独立的本地资料问答、旧论文默认索引及其专用评测已退役；无需准备 `paper/` 目录。PDF 解析、分块、中英文检索和引用校验仍作为 Agent 工具保留。正式研究报告尚未实现，真实模型的研究质量仍需人工验收。

## 本地运行

需要 Python 3.11+ 和 uv。在项目根目录执行：

```powershell
uv sync --locked
```

没有 uv 时，可使用系统 Python 安装 `python -m pip install uv`；已有 uv 的虚拟环境不必额外安装 pip。也可将以下命令中的 `uv` 换成 `python -m uv`。

在项目根目录 `.env` 文件中填写 API Key（已提供空文件，也可复制 `.env.example`）：

```dotenv
OPENAI_API_KEY=你的实际密钥
```

保存后启动：

```powershell
uv run python scripts/app.py
```

网页和命令行共用根目录 `.env` 配置，从其他工作目录启动也能读取。`.env` 已被 Git 忽略，只有空的 `.env.example` 随代码提交。保存密钥后刷新页面即可重新检查配置。

已有的 `OPENAI_API_KEY` 环境变量优先于 `.env`。如果之前在 PowerShell 配置过，先执行 `Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue`，让当前终端改用 `.env`。配置格式为单行 `KEY=value`，可加引号及行末注释，不执行变量替换。不要将真实密钥写入示例文件。

打开 **http://127.0.0.1:8501**，填写主题、首次提交日期范围和问题，点击开始研究。日期范围使用 UTC，起日包含、截止日不包含。建议首次选择 1 篇、预算 0.10 美元。

首次入库需要下载 `intfloat/multilingual-e5-small`；本机已缓存模型时可以运行：

```powershell
uv run python scripts/app.py --local-models-only
```

`--local-models-only` 只限制 embedding 模型加载；联网发现仍访问 arXiv，研究决策仍调用 OpenAI 并计费。只打开页面、查看状态不会调用模型。服务绑定本机地址，同一时间只执行一个任务。

命令行也可发起任务：

```powershell
uv run python scripts/run_research.py --task-id research-demo-01 --topic "RAG hallucination detection" --start 2026-09-01T00:00:00+00:00 --end 2026-10-25T00:00:00+00:00 --question "说明一篇相关论文的问题和主要方法，并引用正文证据" --target-papers 1 --max-cost-usd 0.10
```

新运行使用新的任务 ID。`--resume` 显式恢复兼容的检查点；变更处理代码或协议后不应续跑旧任务。`--offline-sources` 只允许使用本地文献缓存，**LLM 仍然联网计费**。失败任务不会自动重试。

## 工作流程与架构

```text
研究任务 → 模型选择动作 → 搜索候选 → 选择固定版本 → 获取 PDF
        → 解析和任务内入库 → 检索 / 读页 → 引文校验 → 短证据回答
```

这是参考 pi 分层思想、自行实现的 Python 单 Agent harness。通用核心管理消息、工具注册、执行循环和事件；研究宿主管理预算、完成条件、检查点和恢复。使用 GPT-5.4-mini，通过受控的 `research_action` 选择七个研究工具或结束任务。详见 [Agent 结构说明](src/paper_assistant/agent/README.md)。

每个研究任务只检索自己的全文索引，不会读取旧论文或其他任务的索引。保留 Dense、BM25、Hybrid 检索；默认 embedding 支持中英文，实际跨语言效果仍需具体语料验证。PDF 下载成功之后仍必须成功入库才能检索。

引用检查验证证据 ID、页码来源和逐字引文匹配；它不等于验证回答的全部推理或语义正确性。预算是根据 token 用量和记录费率估算，包含报告预留；正式报告尚未使用该预留。未知请求结果按预留保守记账并停止。

## 目录与数据

```text
src/paper_assistant/
  agent/          # 通用执行核心、研究宿主、工具、模型适配和引文校验
  documents/      # PDF 解析、清洗、分块、文档快照
  retrieval/      # Dense、BM25、Hybrid 和检索范围
  literature/     # arXiv 搜索与固定版本下载
  web/            # 本地研究页面及任务 API
  storage.py      # 原子写入
scripts/          # 七个 Agent 相关命令入口
tests/            # 离线测试及脱敏来源快照
eval_data/research/# Agent 开发场景与人工验收用例
```

本地运行会生成 `data/models/`、`data/literature_search/`、`data/paper_cache/` 和 `data/research_runs/`。任务目录保存全文索引、来源、工具日志、预算、消息和检查点。这些数据、PDF、密钥及本地笔记不上传 GitHub。

旧论文、默认索引、旧版脚本及专用评测资料已移至本机忽略目录 `tmp/legacy-rag-20261025/`。它们不参与运行。Agent 的缓存和研究记录保留；RAGTruth 固定历史示例属于 Agent 测试材料，可按需使用，与旧 `paper/` 语料无关。

[命令入口](scripts/README.md) · [Agent 测试资料](eval_data/README.md) · [人工验收步骤](eval_data/research/MANUAL_TESTS.md)

## 验证

```powershell
uv run pytest -q
node --test tests/research_client.test.cjs
```

自动测试使用生成的 PDF、固定来源快照和模拟模型，不需要原测试论文，不调用付费模型。`eval_data/research/` 应随代码保留，便于复现测试意图；虚构候选、待人工复核任务和真实来源样例分别标明，不能将测试通过当作真实 Agent 质量评分。
