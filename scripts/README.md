# Agent 命令入口

所有命令在项目根目录运行：`uv run python scripts/<脚本名>.py --help`。

| 入口 | 用途 |
| --- | --- |
| `app.py` | 启动本地文献研究页面 |
| `run_research.py` | 使用真实 OpenAI 决策执行研究任务 |
| `run_mock_research.py` | 使用模拟策略检查执行循环 |
| `search_literature.py` | 搜索 arXiv 元数据 |
| `fetch_papers.py` | 获取已登记候选的固定版本 PDF |
| `ingest_research_papers.py` | 将选定全文写入独立任务知识库 |
| `run_tool_calls.py` | 显式执行或重放研究工具动作 |

真实研究需要在项目根目录 `.env` 中填写 `OPENAI_API_KEY`，已有环境变量优先；`--help` 不发起模型或文献请求。优先从 `app.py` 或 `run_research.py` 开始，其他入口用于分步调试。参数以各脚本 `--help` 为准。

旧版本地论文问答、全局索引和专用检索评测脚本已退役。Agent 不需要 `paper/` 或默认索引。更改处理代码后使用新的任务 ID。详细说明见 [项目 README](../README.md)。
