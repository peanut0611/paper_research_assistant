# 运行入口

在项目根目录使用统一入口：

```powershell
uv run python -m paper_assistant --help
uv run python -m paper_assistant web --local-models-only
uv run python -m paper_assistant research --help
```

`scripts/app.py` 和 `scripts/run_research.py` 保留为兼容入口，分别转交包内的 `web.cli` 和 `research.cli`，无需重复维护业务逻辑。网页后台直接启动包内研究命令。

单项能力与开发工具集中在 `scripts/dev/`：

| 脚本 | 用途 |
| --- | --- |
| `search_literature.py` | 搜索 arXiv 元数据 |
| `fetch_papers.py` | 获取已登记候选的固定版本 PDF |
| `ingest_research_papers.py` | 将选定全文写入独立任务知识库 |
| `run_tool_calls.py` | 显式执行或重放研究工具动作 |
| `run_mock_research.py` | 使用模拟策略检查执行循环 |

例如：`uv run python scripts/dev/search_literature.py --help`。各脚本的 `--help` 不发起模型或文献请求。

真实研究在根目录 `.env` 填写 `OPENAI_API_KEY`，已有环境变量优先。处理代码变更后使用新任务 ID；当前目录变更会触发旧工作区的指纹保护。详见 [README](../README.md) 和 [结构说明](../ARCHITECTURE.md)。
