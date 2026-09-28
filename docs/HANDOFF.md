# HANDOFF（公开版进度记录）

这里只记录可以公开的进度。涉及真实持仓、个人 memo 和账户的完整会话记录在 `private-data/HANDOFF.md`（git 忽略，不提交）。

## 2026-09-27：v1 核心（Phase 0–1）

- `docs/DESIGN.md` v1：范围、部署拓扑、数据模型、规则引擎、交易前闸口、memo 状态机、LLM 层、Lab、分阶段计划。
- `investment_core`：模型、15 类规则、规则引擎、交易前闸口（自动项 + 自我确认项，结果分 clear / incomplete / warnings / rule_breaks）、memo 状态机（AI 不能写 §A–§D）、复盘报告、CSV 导入、CLI。
- 虚构 fixture：三个 demo 组合、demo 规则集（break 级）和草拟规则集（只发 warn/info）。测试不使用真实代码加真实比例。

## 2026-09-27：SEC 财报管道（Phase 4）

- `investment_data`：EDGAR 客户端（User-Agent、限速、文件缓存）。`investment_core.financials`：companyfacts 解析、年初至今数字差分为单季、推导值带公式和来源。
- 黄金测试：GOOG、TSLA、MSFT、MU 的季度数字与财报新闻稿逐一核对。

## 2026-09-27 至 09-28：memo 生成与 AI 反方（Phase 5 私有部分）

- 证据包（每条事实有编号）；模型输出校验：结构、数字核对、中英文禁止买卖建议、事实与解读分开、未引用来源的专有名称拦截、逐字引用核对；失败重试一次后拒绝输出；调用记录和月度预算。
- prompt 迭代到 `research_skeptic_v5`，改动都来自人工审查真实草稿时发现的问题（例如把非经营收益带来的净利润大增当作经营改善）。
- Step 6（审查用户回应，仓位检查交给规则引擎）、Step 7（定稿）、示例论点。
- 资产负债表数据、分部数字（申报文件内的维度数据）、10-K/10-Q 文字检索（BM25）。
- 13F 机构持仓追踪（Phase 4b），Berkshire 2026 Q2 结果与公开报道一致。

## 2026-09-28：公开 Lab 上线

- `investment_api`（FastAPI）+ `frontend/`（Vite + React）：财报快照、交易前闸口，使用虚构组合和 10 家公司的公开数据；不读私有数据；限流；默认关闭 API 文档。
- 部署文件与验证见 DESIGN §14.1 和 `docs/DEPLOY.md`：独立网络、只读容器、白名单构建上下文、`test_deploy.py`、`check-release.sh`。
- `https://invest.jun-liang-lyu.com` 已上线，发布检查 13 项全部通过；80 端口已开放，HTTP 自动跳转 HTTPS。
- 已知限制：SEC companyfacts 可能滞后于申报；COST 没有财季标签。
- 测试 191 个全部通过。

## 下一步

- self_web 的 Lab 卡片和项目案例（内容已写好，待发布）。
- 公开仓库前的隐私检查（本文件即为清理后的版本）。
- ≥ 30 条对抗性 eval 用例，然后把 AI 反方放到 Lab（每 IP 限流、日/月预算、30 天清理）。
