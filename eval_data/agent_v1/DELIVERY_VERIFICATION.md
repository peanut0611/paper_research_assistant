# Agent V1 交付核验记录

日期：2026-11-04。对应 Issue #9；本记录只证明列明的安装和只读命令行为，不作为真实研究质量验收。

## 隔离环境与安装

以本地 integration `74896b9` 为源，使用 `git clone --local --no-hardlinks` 创建临时 checkout；未复制原工作区的 `.venv`、`data` 或 `.env`。这验证本地待交付源码，未验证远端分支已同步。源项目始终保留，本地用户文件未被覆盖。

执行：

```bash
uv sync --locked --cache-dir /workspace/.cache/uv --link-mode symlink
uv run python -m paper_assistant --help
uv run python -m paper_assistant research --help
uv run python -m paper_assistant.evaluation --help
uv run python -m paper_assistant web --local-models-only
```

隔离虚拟环境安装成功，共 75 个包。复用了已有 uv 下载缓存，以 symlink 链接包；最初 copy 模式因临时磁盘空间不足失败，清理本次临时虚拟环境后改用链接安装成功。它验证锁文件安装和独立环境中的导入，不证明所有依赖或 embedding 权重已重新联网下载，也不证明模型权重身份可复现。

环境实测：Python 3.12.14、uv 0.12.23、Node.js v24.19.0。支持范围见 README；CI 选择 Python 3.12 和 Node.js 22。本机记录不能预先当作远端 CI 运行通过。

真实 loopback HTTP 服务中，`/`、`/research.js`、`/conversation.js`、`/api/research/status`、`/api/research/jobs` 均返回 200；状态显示 Key 未就绪。未发起研究/追问 POST，模型调用为 0。`--local-models-only` 下启动与只读页面检查不加载正文入库权重。

## 合入比较器后的复核

本地隔离 clone 从其本地 origin fetch 后切换到 integration `964661f89d19a383c8d2e5b8e6c1ba2da96598bb`（已含 Issue #8 工具代码）。仍未复制 `.env` 或运行数据；Python 实际导入路径确认位于隔离 clone 的 `src/paper_assistant`。没有重新创建已验证的虚拟环境，复用同一缓存链接安装。

在该 checkout 中再次核验以下 `--help`，全部退出码 0：主入口默认/web/research/chat、`paper_assistant.evaluation`、`paper_assistant.material_preparation`、`paper_assistant.evaluation_review` 及其 review/compare。最终比较器命令参数与五例 README 一致；生成比较记录不等于真实案例通过。

以该 clone 的真实 `PaperHTTPServer` 启动临时 loopback 服务，再次 GET `/`、`/research.js`、`/conversation.js`、`/api/research/status`、`/api/research/jobs`，均为 200，`key_ready=false`。没有替换 HTTP handler 或业务服务；没有提交任何研究/追问 POST。首次默认沙箱禁止创建 socket，本次仅为 loopback 检查启用工具网络权限后通过，未请求外部域名。

两份 Node 客户端测试在交付工作树通过；README 的本地链接均存在，提交前 diff 空白检查通过。未把这些行为检查计入五例真实模型效果。

## 可复现方法

从待交付分支新建 checkout，避免复制旧 `.env`、虚拟环境及数据；按 README 安装。可复用 uv 缓存，但须在自己的记录中明确缓存/网络条件。启动服务后可在另一个终端执行以下只读检查（服务默认端口 8501）：

```bash
curl --fail http://127.0.0.1:8501/ -o /tmp/paper-assistant-index.html
curl --fail http://127.0.0.1:8501/research.js -o /tmp/paper-assistant-research.js
curl --fail http://127.0.0.1:8501/conversation.js -o /tmp/paper-assistant-conversation.js
curl --fail http://127.0.0.1:8501/api/research/status
curl --fail http://127.0.0.1:8501/api/research/jobs
```

不要把包含用户路径或运行内容的完整响应直接提交。无 Key 和权重时只验证这些只读操作；需要完整用户流程则按 README 配置自己的 Key、真实 embedding 资源与网络后人工运行。

## 自动与人工证据的范围

无费用回归命令为 `uv run pytest -q` 和 README 中的两份 Node 客户端测试；CI 不配置密钥，禁止 Hugging Face 在线加载。自动回归的模型、PDF 或来源替身只验证业务行为。最终集成测试数量由最终集成运行另行记录，本文件不预填结果。

真实用户流程尚待：在线发现和 PDF 获取、正文入库、真实初始回答及连续追问、引用页原文和结论支持度人工核查、真实执行中的停止、新建/切换/删除确认，以及固定五例 review/compare 的有效基线对照。现有自动化行为覆盖不能替代这些真实效果验收。

当前缺少有效改造前真实模型基线，固定材料 PDF/人工页证据未齐；本次没有验证真实 Key、外网链路、embedding 首次下载或人工质量得分。Issue #8 仍未满足，不能将交付文档、预检、模拟执行或对照工具可运行写成五例通过。公开后续结果前逐项人工脱敏，只提交必要的来源元数据、短原文、评分和汇总，不提交密钥、权重、PDF 或完整私有运行目录。
