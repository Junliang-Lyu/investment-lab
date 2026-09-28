# AGENTS.md

开始任何工作前，先读 `docs/DESIGN.md`（设计）和进度记录：`private-data/HANDOFF.md`（完整记录，如果存在）或 `docs/HANDOFF.md`（公开版）。设计有变化时，先改 DESIGN.md，再改代码。

## 硬性边界

- 不写任何下单、改单、撤单代码，不接入有交易权限的券商凭据。
- 系统不输出买入/卖出/持有建议或目标价。LLM 输出必须经过 `DESIGN.md` §10.4 的校验才能展示。
- 规则判断只用确定性代码（`investment_core`），不交给 LLM。
- memo 的 §A–§D（配置理由、反方回应、失效条件、复盘日期）只能由用户填写，AI 不能写入这些字段。
- 公开接口 `/api/lab/*` 不能读取私有数据。
- 不提交 `private-data/`、`.env*`、真实持仓文件或任何密钥。测试 fixture 一律用虚构代码（AAAA、BBBB…）或虚构组合，不用真实代码加真实比例。

## 工作方式

- 按 `DESIGN.md` §16 的 Phase 顺序推进，每个 Phase 以验收标准为完成条件。
- `investment_core` 先写测试再写实现，保持无 IO、可单独测试。
- 每轮工作结束，在 `private-data/HANDOFF.md` 追加日期、已完成、已验证的内容和下一步；只把不含个人信息的摘要写进 `docs/HANDOFF.md`。
- 仓库是公开的：提交的文件（包括文档、测试注释、提交信息）里不能出现真实持仓、仓位比例、个人 memo 内容或账户信息。
- 部署相关的修改要与 self_web `docs/DEPLOYMENT.md` 保持一致：在本地构建，服务器只接收构建产物。
