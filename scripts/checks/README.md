# 工程检查（A1）

这些检查度量工程回归，不度量真实研究质量。模型、来源网络和 embedding 在现有测试中用显式 fixture/依赖注入替代；生成 PDF 和本机 HTTP/子进程仍实际运行。

## 入口

```powershell
uv sync --locked
uv run --no-sync python scripts/check.py
uv run --no-sync python scripts/checks/scan_secrets.py
```

`uv sync --locked` 是联网安装阶段，锁不匹配会失败，不自动升级。日常已有环境可直接 `.venv/Scripts/python.exe scripts/check.py`；Linux 使用 `.venv/bin/python`。不要为了测试把 API Key 添加到 CI。

统一入口需要 Node.js，并分别运行 Python 与前端套件。某一套件失败仍运行另一套件，最终退出码非零。每个套件最多 600 秒，CI job 最多 25 分钟。不会递归删除用户目录；每次创建新的 `tmp/checks/run-*`，包含 JUnit、日志和注明 `engineering_regression` 的 summary.json。

Python 测试通过 startup guard 和 conftest 安装审计钩子：

- 在 socket/DNS 边界拒绝外网，允许 loopback 服务；Python 子进程即便重设 PYTHONPATH 也继承 guard。
- 拒绝打开仓库根目录真实 `.env`（在文件内容读取前阻断）；临时测试目录 `.env` fixture 仍可测试。
- 统一入口给子进程空 OPENAI_API_KEY，并去除代理变量和常用模型凭据；清空只作用于子进程环境，不修改 shell、用户文件或程序正常启动行为。
- 设置 HF/Transformers 离线模式。Node 预加载 guard 阻断真实 fetch/http/socket，VM 注入的测试 fetch 不受影响。
- 禁用 pytest 第三方插件自动加载，避免本地插件改变回归行为。

这是针对可信测试代码的误调用保护，**不是 OS 安全沙箱**：不能保证拦截恶意代码、所有原生库网络或任意非 Python 子进程。已有用例覆盖外部域名/IP、子进程覆盖环境、真实配置误读、Node 请求和失败退出码。loopback 可用性仍受执行环境权限影响，不应把 WinError 10013 简化为业务缺陷或删除测试。

## CI 与依赖检查

`.github/workflows/checks.yml` 在 main push、PR 和手动操作触发，只有 contents:read，checkout 不保留凭据。Actions 固定到核验的提交 SHA；Python 3.12.8、Node 22.14.0、uv 0.13.1。Windows 和 Ubuntu 独立执行，取消同分支旧运行，缓存仅用于 pip 引导工具；大模型/论文/用户运行数据不入缓存。首次 uv 依赖安装不依赖缓存。

上传目录仅包含统一入口生成的测试报告，保留七天；不上传整个 tmp/、data/、.env 或 PDF。测试失败输出只应包含合成测试数据，新增测试不得把用户材料写入断言。原始测试日志不等于可公开的用户运行日志。

依赖审计单独允许查询公开漏洞库：

```powershell
uv run --no-sync python scripts/checks/audit_requirements.py
uv tool run --from pip-audit==2.9.0 pip-audit --no-deps --disable-pip -r tmp/audit-requirements.txt
```

CI 在 locked sync 后导出当前平台实际安装版本，避免把跨平台不可安装的锁分支硬拼成 requirements。开发者环境含锁外包时也会审计这些包，需与干净环境区分。pip-audit 在隔离工具环境安装，不进入业务依赖。发现漏洞或联网失败均非通过；禁止 `continue-on-error` 隐藏风险。只查询，不自动修复依赖。

`scan_secrets.py` 是基础高置信模式门禁：已跟踪文件中的 OpenAI/GitHub/AWS 凭据模式和私钥头。危险配置文件名、符号链接在读取前拒绝；输出仅路径、行号与脱敏原因。它不读取被忽略的真实 `.env`，也不扫描 Git 历史、熵型通用密码或提供“仓库无秘密”的保证。未跟踪新文件在提交后才进入该扫描范围。发现真凭据应撤销/轮换；误报需具体定位和人工评估，不用整目录白名单绕开。

配置参考：[uv locked sync](https://docs.astral.sh/uv/concepts/projects/sync/)、[pip-audit 官方文档](https://github.com/pypa/pip-audit)。

## 本轮结果与待完成验证（2026-11-04）

- 保留前端四个未提交改动；没有修改 Agent、索引、预算或业务依赖锁。
- 本机 Python 3.12.8 / Node 20.19.0：556 Python 测试通过，1 项因 Windows 符号链接权限跳过；25 项 Node 测试通过。统一入口验证了故意失败的临时测试返回失败且后续前端检查仍执行。
- 在 `tmp/a1-clean-env` 使用 `uv sync --locked --python 3.12.8` 安装成功，共 57 个包；原 `.venv` 未变。缺少的缓存包只在安装阶段下载。随后干净环境完整回归同样为 556 passed、1 skipped（108 秒）与 25 项 Node 通过；结果位于 `tmp/checks/run-fv1zsanp/`。最终环境报告字段改动后另跑检查基础设施的 9 项测试通过。
- 基础凭据检查：已跟踪源码零命中，未打开真实密钥文件。
- 本机现有环境审计发现四个包共 34 条公告：certifi 2026.7.22、filelock 4.0.7、pypdf 6.19.0、setuptools 84.0.0。这四个版本也在干净锁定环境中。公告给出的修复版本分别至少为 2026.10.18、4.2.1、6.21.4、84.1.0；实际修复需再核对最新公告和约束，不能只为消红升级到任意版本。
- 安全门禁目前会失败，这是已暴露的真实待办，不是回归测试失败。应单独实施依赖修复、重跑解析/索引与完整回归；pypdf 版本属于 workspace processing 身份，旧写任务必须保持版本核对，旧索引只读兼容不应被破坏。
- 远端 Actions、Linux、CI Node 22 和分支保护尚未实跑；未推送，不能声称 GitHub 发布门禁已上线或项目生产就绪。

## 后续顺序

先处理独立的依赖安全修复并验证旧任务只读兼容；A2 可同时推进 gold schema/来源清单，但人工复核结果不能由程序伪造。A3 的真实模型基线仍需要单独授权和总预算。CI 通过不能替代这些质量验收。
