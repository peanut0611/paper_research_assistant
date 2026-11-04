# Issue #8 公开验收记录

`preflight-20261104/online/report.json` 与 `fixed/report.json` 为 2026-11-04 09:21 UTC 的只读预检记录：只请求本地 Web status，没有请求研究/追问，没有真实模型调用或人工质量评分。保留当时源码 HEAD 与工作区差异指纹、冻结输入 SHA、原始研究输入和具体阻塞；不是当前配置就绪状态的保证。

`baseline-unavailable/report.json` 是原始真实模型基线缺失的显式引用，不是一次运行。它引用原始冻结标识且 cases 为空，使缺失案例在比较输出中保持可见。不能在改造后补跑后改名为原始基线。

`compare-fixed/` 与 `compare-online/` 由 review/compare 工具依据上述记录生成：整体 blocked，五例均未形成可计分证据，通过率 null。即使 evidence_kind 的请求目标为 real_model_http，execution_status=not_executed 且无模型事件也不能证明模型执行。固定材料缺 PDF/seed/页级原文；历史预检还记录无 Key 和网络策略阻塞。未伪造模型输出、论文证据、人工评审或提升数值。

新配置、新材料就绪后应另存新预检及真实采集目录；原记录不覆盖。实际模型费用、人工结论支持度、真实在线 arXiv 链路、管理操作的真实人工验收仍待完成。自动化 Web/采集/工具边界测试独立报告，不替代它们。

复现命令及人工评审字段见上级 README。比较 JSON 保留逐轮轨迹和人工记录；公开真实运行前仍需检查路径、凭证、无关用户内容并脱敏。源码指纹只能识别工作区差异，不能恢复源码。
