# Investment Lab — 设计文档 v1

> 状态：草案，待实现
> 日期：2026-09-27
> 适用范围：本仓库所有代码、部署和 Lab 体验。实现时以本文档为准；改设计先改本文档。
> 上游资料：`../personal_investment_system_v0/`（v0 规则、SOP、memo）

---

## 0. 一句话

把 v0 的 Markdown 投资纪律流程做成一个可运行的系统：私有部分是我自己的持仓、规则和 memo；公开部分是 Lab 里的四步体验（财报快照 → 反方审查 → memo 草稿 → 实盘前闸口）。系统不推荐买卖，只让每个决策留下证据、反方和记录。

---

## 1. 背景：v0 为什么不够

v0（2026-06）完成了规则、memo SOP、实盘前检查清单、组合复盘 SOP，以及两份个股 memo。三个月后的实际情况：

- 每月复盘没有发生过一次，持仓登记表三个月没有更新。
- 加仓时没有走实盘前检查清单，仓位集中在少数几只同主题的个股上。
- v0 的规则数值只是草拟，本人并不打算严格照做。

结论：问题不在规则写得好不好，而在于**规则不在做决定的那一刻出现**，而且**事后没人发现规则被跳过**。所以 v1 的重点是：

1. **事后可见**：导入持仓后自动发现"没做闸口检查的加仓"和"超期未复盘"。
2. **规则可改**：规则是有版本号的数据，不是写死的常量。草拟规则也能跑，只产生警告。
3. **覆盖留痕**：允许不按规则来，但要写一句理由，记进日志。系统本来就不下单，也不阻止下单，它只保证"跳过"这件事被记录下来。

---

## 2. 目标与非目标

### 目标

- G1 私有 dashboard：持仓快照、规则越线、memo 进度、待办和复盘日期。
- G2 规则引擎：确定性代码，规则版本化，输出越线列表。
- G3 实盘前闸口：输入一笔拟交易，输出按检查清单逐项的结果。
- G4 memo 状态机：对应 memo SOP 的 Step 1–7，状态转换有校验和审计记录。
- G5 公开 Lab 四步体验，用公开财报数据和虚构组合，任何人都能试。
- G6 SEC EDGAR 数据管道：结构化财报数字，每个数字都能追溯到原始文件。
- G7 LLM 层：反方审查、memo 审查，输出固定 schema、只能引用证据、自动校验、全程记录（`ai_runs`）、有离线 eval。

### 非目标（v1 明确不做）

- 下单、改单、撤单，或存储任何有交易权限的券商凭据。
- 实时行情、K 线、价格推送。
- 买入/卖出/持有建议、目标价、荐股。
- 在服务器上跑 Qlib 训练或 LEAN 回测（这类计算在本地完成，只上传结果）。
- 多用户、注册、权限体系（私有部分只有我一个用户）。

---

## 3. 两种模式和数据边界

| | 私有模式 `/app` | 公开 Lab `/lab` |
|---|---|---|
| 使用者 | 只有我 | 任何访客 |
| 持仓数据 | 真实 IBKR 导入 | 仓库内的虚构组合（明确标注虚构） |
| 财报数据 | EDGAR 管道 | 同一个 EDGAR 管道 |
| memo | 持久保存、版本化 | 仅当前会话，可下载 Markdown，不入库（反方审查结果按保留策略短期存储） |
| LLM 调用 | 有，个人预算 | 有，严格限流和每日预算上限 |
| 认证 | 登录 + session cookie | 无 |

硬性边界：

- 公开接口 `/api/lab/*` 永远不能读取私有表（portfolio kind=`private` 的任何数据）。在查询层强制过滤，并写测试。
- 真实持仓文件只放在 `private-data/`（已 gitignore）。仓库里的测试 fixture 一律使用虚构代码（AAAA、BBBB…）复现持仓形态，不使用真实代码加真实比例。真实数据的回归测试只在 `private-data/` 存在时运行。
- 仓库公开到 GitHub，但个人内容不进仓库：真实持仓、v0 的 memo 原文（留在仓库外的 `../personal_investment_system_v0/`，导入器在运行时读取）、`.env*` 都不提交。

---

## 4. 部署拓扑

沿用 self_web 现有的单台 Lightsail（Small-2GB，us-east-1）和 Docker Compose，新增一个容器和一个子域名。

```
Internet
  │
  ▼
Caddy（现有，80/443）
  ├─ jun-liang-lyu.com          → 现有静态站 + /api/* → registry:8080（不改）
  └─ invest.jun-liang-lyu.com   → 新 site block
        ├─ /api/*  → investment-api:8081（FastAPI，新增）
        └─ /*      → /srv/invest（前端 SPA 静态文件，新增）

backend 网络（internal）
  ├─ registry（现有）
  ├─ investment-api（新增，内存上限 256M）
  └─ postgres（现有容器）
        ├─ database portfolio_registry（现有，registry 用户）
        └─ database investment（新增，investment_app 用户）
```

访客看到的路径（和 Classic Snake 一样）：

```
jun-liang-lyu.com/en/lab             Lab 列表里多一个 Investment Lab 条目
  → /en/lab/investment-lab           self_web 的详情页（MDX：介绍、截图、Try it 链接）
  → invest.jun-liang-lyu.com/lab     点 Try it 进入应用本身
```

对访客来说，它就是个人网站 Lab 下的一个条目。self_web 只需要新增一个 MDX 文件，现有的 Lab schema（`type`、`demo`、`github`）已经够用，不用改代码。应用本身需要后端，所以部署在子域名上。

设计理由：

- **不放进 self_web 仓库。** self_web README 规定新增内容要 "remain incremental additions rather than a separate dashboard or SaaS redesign"，并且有 professional/creator 隐私审计和主题测试。需要登录、读私有数据的应用应该单独部署，这和 Classic Snake 的模式一致（独立部署 → Lab 卡片链接 → registry 登记）。
- **用子域名，不用 `/api/investment/*`。** cookie 和认证与主站完全隔离；Caddy 规则不用和 registry 的 `/api/*` 排优先级。
- **独立 database 和用户。** registry 的数据库账号无法连接 investment 库，反之亦然。

---

## 5. 仓库结构

```
investment-lab/
├─ AGENTS.md                  agent 工作约定（指向本文档）
├─ README.md                  运行说明（Phase 1 时写）
├─ docs/
│  ├─ DESIGN.md               本文档
│  └─ HANDOFF.md              每轮工作结束写交接（格式同 self_web）
├─ backend/
│  ├─ pyproject.toml
│  ├─ src/investment_core/    纯领域逻辑，不依赖 web/DB
│  │  ├─ models.py            pydantic 模型
│  │  ├─ rules.py             规则实现，每个 rule code 一个函数
│  │  ├─ engine.py            evaluate()
│  │  ├─ gate.py              evaluate_trade()
│  │  ├─ memo.py              memo 状态机
│  │  ├─ review.py            复盘报告（确定性部分）
│  │  ├─ importers/           IBKR / 简单 CSV / v0 导入
│  │  └─ cli.py
│  ├─ src/investment_api/     FastAPI、SQLAlchemy、认证、LLM、EDGAR、定时任务
│  ├─ alembic/
│  ├─ prompts/                版本化 prompt（skeptic_v1.md …）
│  ├─ evals/                  eval 数据集和运行脚本
│  └─ tests/
├─ frontend/                  Vite + React + TypeScript
├─ fixtures/
│  ├─ demo_portfolios/        公开 Lab 用的虚构组合
│  └─ rules/v0_draft.yaml     v0 草拟规则
├─ deploy/                    compose 片段、Caddy 片段、DB 初始化 SQL、脚本
└─ private-data/              真实持仓导出（gitignore，不提交）
```

技术栈：Python 3.12、FastAPI、pydantic v2、SQLAlchemy 2 + Alembic、psycopg 3、pytest、httpx；前端 Vite + React + TS、TanStack Query、一个轻量图表库。

`investment_core` 必须能脱离数据库单独运行和测试，API 层只负责持久化和编排。

---

## 6. 核心领域模型（`investment_core`）

### 6.1 实体

- **Asset**：`symbol`、`name`、`asset_type`（stock/etf/cash/other）、`sleeve`（core/satellite/unclassified）、`exposure_tags`（用户自定义主题，例如 `us_megacap_tech`）、`cik`（可选）。
- **Snapshot**：`as_of`、`net_liquidation`、`cash`、`positions[]`、`source`（ibkr_flex / csv / manual / fixture）。
- **Position**：`symbol`、`quantity`、`market_value`、`cost_basis`（可选）、`unrealized_pnl`（可选）、`currency`。
- **RuleSet**：`version`、`status`（draft/active/retired）、`rules[]`。
- **Rule**：`code`、`params`、`severity`（info/warn/break）。
- **Violation**：`rule_code`、`symbol`（可选）、`observed`、`limit`、`severity`、`message`。
- **Memo / MemoVersion**：见 §6.4。
- **TradeProposal**：`symbol`、`side`、`amount_usd` 或 `quantity`、`price`、`note`。
- **GateResult**：见 §6.3。
- **Override**：`target`（violation 或 gate 项）、`reason`（必填）、`created_at`。

关于行业：**不要用 GICS 行业做集中度判断。** 例如按 GICS，Alphabet 属于 Communication Services，Tesla 属于 Consumer Discretionary，算下来"行业集中度"很低，但实际两者都是美股大型科技/AI 暴露。所以集中度规则按用户自定义的 `exposure_tags` 计算，GICS 只作展示参考。

### 6.2 规则引擎

```python
def evaluate(snapshot: Snapshot, rule_set: RuleSet, ctx: Context) -> list[Violation]
# ctx: 当前 memo 状态、watchlist/paper 记录、上次复盘日期、上一次快照（用于做差）、gate 记录
```

纯函数、无 IO、结果确定。每个 rule code 对应一个函数，参数来自 RuleSet。**任何规则判断都不交给 LLM。**

同时计算两种口径：占净值（÷ net_liquidation）和占已投入（÷ 持仓总市值），对应复盘 SOP §5。

目前没有明确的持仓计划：现在以小仓位为主，打算先参考更多别人的配置方案再定规则。所以私有模式下 v0 草拟规则全部以 `warn` 或 `info` 级别运行，不产生 `break`，其中 `CORE_TARGET_WEIGHT` 为 `info`。规则定稿后再调整级别。

v0 草拟规则（`fixtures/rules/v0_draft.yaml`，status=draft）：

| rule code | 含义 | v0 草拟参数 | 用于 |
|---|---|---|---|
| `CORE_TARGET_WEIGHT` | Core 占净值低于目标 | 70% | 快照 |
| `SATELLITE_MAX_WEIGHT` | Satellite 占净值上限 | 30% | 快照、闸口 |
| `SINGLE_MAX_WEIGHT_NAV` | 单一标的占净值上限 | 15% | 快照、闸口 |
| `MEMO_REQUIRED_ABOVE` | 超过该占比必须有已审查的 memo | 10% | 快照、闸口 |
| `EXPOSURE_MAX_WEIGHT` | 单一 exposure tag 占净值上限（默认不计 Core） | 35% | 快照、闸口 |
| `SINGLE_MAX_WEIGHT_INVESTED` | 单一 Satellite 占已投入上限（Core 豁免） | 50% | 快照 |
| `TOP3_MAX_WEIGHT_INVESTED` | 前三大非 Core 持仓占已投入上限（默认不计 Core） | 90% | 快照 |
| `LOSS_REVIEW_TRIGGER` | 单一持仓浮亏达到金额即生成复盘待办 | $600 | 快照 |
| `FIRST_LIVE_MAX_USD` | 首次实盘观察仓金额上限 | $1,000 | 闸口 |
| `PAPER_MIN_WEEKS` | paper/watchlist 最短观察期 | 6 周 | 闸口 |
| `PAPER_MIN_REVIEWS` | 转实盘前最低复盘次数 | 2 次 | 闸口 |
| `REVIEW_OVERDUE` | 实盘组合复盘超期 | 每月 | 快照 |
| `UNCLASSIFIED_POSITION` | 有持仓未归入 Core/Satellite | — | 快照 |
| `MISSING_INVALIDATION` | Satellite 持仓的 memo 缺少失效条件 | ≥3 条 | 快照 |
| `UNCHECKED_TRADE` | 两次快照之间某持仓增加，但之前 N 天内没有对应的闸口记录 | N=7 天 | 快照做差 |

`EXPOSURE_MAX_WEIGHT` 和 `TOP3_MAX_WEIGHT_INVESTED` 默认不计 Core（参数 `count_core: false`）：宽基指数基金是分散的底仓，不是主题押注；否则一个纯指数组合也会被误报。

注意："占已投入"口径的规则在大部分是现金的组合里一定会触发（一两只股票就是已投入的 100%）。这是当前行为，规则定稿时再决定是否保留。

`UNCHECKED_TRADE` 是 v1 最重要的规则：系统拦不住你在 IBKR 下单，但下一次导入时一定会把"没走闸口的加仓"标出来。

规则数值会改（见 §17），代码不能依赖这些具体数字。每条 Violation 记录判定时用的 `rule_set_version`，改规则不会影响历史判定。

### 6.3 实盘前闸口（`gate.py`）

```python
def evaluate_trade(snapshot, proposal, rule_set, ctx) -> GateResult
```

步骤：用拟交易生成模拟快照 → 对模拟快照跑 §6.2 的快照规则 → 按实盘前检查清单逐节生成检查项。

`GateResult.items[]` 每项：`section`（对应检查清单 §4–§10）、`label`、`kind`（auto / self_attest）、`status`（pass / fail / unknown）、`detail`。

- **auto**：代码能判断的项目，例如交易后是否越线、memo 是否存在且状态合格、观察期和复盘次数是否达标、金额是否超过首次观察仓上限。
- **self_attest**：只能本人确认的项目，例如"不是 FOMO""不是为了摊低成本""下跌 20–30% 不影响生活"。默认 `unknown`，用户勾选后变为 `pass`。

总体结论有四种：`rule_breaks`（有 break 级失败）/ `warnings`（有 warn 级失败）/ `incomplete`（没有失败，但还有自我确认项未勾）/ `clear`（全部通过）。**不会输出"可以买"。**

集中度检查只把"这笔交易新造成或加重的越线"判为失败；交易前就存在、且没有被这笔交易加重的越线，只在说明里注明。 私有模式下每次闸口检查都会存进 `gate_checks`，包括覆盖理由，供 `UNCHECKED_TRADE` 做匹配。

### 6.4 memo 状态机（`memo.py`）

状态与 memo SOP 步骤一一对应：

```
idea ──► researching ──► skeptic_done ──► user_responded ──► reviewed ──► final
(Step1)   (Step2-3)       (Step4)          (Step5 §A-§D)      (Step6)      (Step7)
                                                                             │
任意状态 ──► archived                                   final ──► researching（新版本，例如财报后重审）
```

转换条件：

- `→ skeptic_done`：必须有 ≥3 条反方理由和"最脆弱的关键假设"。
- `→ user_responded`：§A 配置理由（含目标仓位）、§B 对 3 条反方逐条回应、§C 至少 3 条失效条件、§D 复盘日期，全部非空。这些字段**只能由用户填写**，API 层拒绝 AI 写入这些字段。
- `→ reviewed`：已有 Step 6 的 AI 审查结果（或用户选择跳过，需要 Override）。
- `→ final`：必须选择一个结论：`watchlist` / `paper` / `eligible_for_gate`。如果 Step 6 有未解决的问题还要选 `eligible_for_gate`，需要 Override 并写理由。

每次转换写一条 `memo_events`（from、to、actor、时间、override_id）。memo 内容用版本号管理，财报后重审生成新版本，旧版本保留。

memo 内容结构（`MemoContent`，JSON）：`one_liner`、`business`（5 个问题的回答）、`evidence_refs`、`bull[]`、`bear[]`、`valuation_context`、`skeptic`（top3、weakest_assumption）、`user.A`、`user.B`、`user.C`、`user.D`、`ai_review`、`decision`。`bull`/`bear` 每条带 `type`（fact / inference / to_verify）和证据引用。

v0 的 memo 导入时：原文整篇存进 `body_md`，只提取状态、复盘日期、是否有失效条件这几个字段，不做完整解析。

### 6.5 复盘报告（`review.py`）

按组合复盘 SOP 的固定输出格式生成确定性部分：账户快照、两种口径的集中度、前三大持仓、Core/Satellite 和 exposure 占比、最大持仓下跌 10%/20%/30% 对净值的影响、规则越线列表、到期待办。输出 Markdown，存进 `portfolio_reviews`。"持仓论点重测"一节 v1 留给用户手写，v2 再考虑让 LLM 辅助。

### 6.6 数据导入

- v1：上传文件。支持 (a) 简单 CSV（`symbol,quantity,market_value,cash`），(b) IBKR 导出的持仓报表。**IBKR 解析器必须以真实导出样本为准来写**，样本放 `private-data/`，测试用脱敏版本。
- v1.5：IBKR Flex Web Service 定时拉取（只读 token，只存在服务器环境变量里）。实现前按 IBKR 官方文档核对接口细节，本文档不假定具体参数。
- 导入完成后自动：生成快照 → 跑规则 → 与上一快照做差 → 生成待办。

---

## 7. 数据库（database `investment`）

所有表都带 `created_at`。私有和演示数据用 `portfolios.kind` 区分。

```
portfolios          id, kind(private|demo), name
assets              symbol PK, name, asset_type, sleeve, exposure_tags text[], cik
snapshots           id, portfolio_id, as_of, net_liquidation, cash, source, raw_file_sha256, imported_at
positions           id, snapshot_id, symbol, quantity, market_value, cost_basis, unrealized_pnl, currency
rule_sets           id, version, status(draft|active|retired), notes
rules               id, rule_set_id, code, params jsonb, severity
evaluations         id, snapshot_id, rule_set_version, evaluated_at
violations          id, evaluation_id, rule_code, symbol, observed, limit_value, severity, message
overrides           id, target_type, target_id, reason, created_at
gate_checks         id, portfolio_id, proposal jsonb, rule_set_version, result jsonb, overall, override_id
memos               id, portfolio_id, symbol, status, current_version
memo_versions       id, memo_id, version, content jsonb, body_md, author(user|import)
memo_events         id, memo_id, from_status, to_status, actor, override_id
watchlist           id, symbol, mode(paper|watch), started_at, review_count
review_tasks        id, kind(monthly|memo|loss|earnings), symbol, due_date, status
portfolio_reviews   id, snapshot_id, report_md, created_at

-- EDGAR（公开数据，私有和 Lab 共用）
companies           cik PK, ticker, name, fiscal_year_end, supported bool
filings             accession PK, cik, form, filed_at, period_end, fy, fp, url
financial_facts     id, cik, metric, concept, unit, period_start, period_end, value,
                    accession, form, fy, fp, derived bool, derivation text

-- LLM
ai_runs             id, surface(lab|private), task, model, prompt_version, input_hash,
                    input jsonb, output jsonb, validation jsonb, status,
                    tokens_in, tokens_out, cost_usd, latency_ms
lab_usage           day, ip_hash, count           -- 每 IP 每日计数
llm_spend           day, surface, spent_usd       -- LLM 花费，按日记录、按月汇总
```

`ai_runs` 在 Phase 2 就建好，即使那时还没有 LLM 调用。

---

## 8. API

健康检查：`GET /api/health` → `{"status":"ok"}`，不返回任何细节，供 registry 做健康检查。

认证：

```
POST /api/auth/login      {password} → 设置 session cookie
POST /api/auth/logout
GET  /api/auth/me
```

私有（全部需要 session）：

```
GET  /api/private/overview                 快照 KPI、越线、未检查交易、到期待办
POST /api/private/imports                  上传持仓文件 → 快照 + 评估
GET  /api/private/snapshots[/{id}]
GET  /api/private/violations?snapshot_id=
POST /api/private/gate                     拟交易 → GateResult（持久化）
POST /api/private/overrides
GET/POST/PATCH /api/private/memos[/{id}]
POST /api/private/memos/{id}/transition    {to, override_reason?}
POST /api/private/memos/{id}/skeptic       Step 4（LLM）
POST /api/private/memos/{id}/ai-review     Step 6（LLM）
GET/POST /api/private/rule-sets            新建草稿、激活、查看历史
POST /api/private/reviews                  生成复盘报告
GET  /api/private/tasks
```

公开 Lab（无认证，限流）：

```
GET  /api/lab/companies                    支持的公司列表
GET  /api/lab/companies/{ticker}/snapshot  最近 8 季度指标 + 证据
POST /api/lab/skeptic                      {ticker, thesis} → 反方审查
POST /api/lab/memo-draft                   {ticker, thesis, skeptic_run_id, user_answers} → Markdown
GET  /api/lab/demo-portfolios
POST /api/lab/gate                         {portfolio_id, proposal} → GateResult（不持久化）
GET  /api/lab/evals/latest                 最新 eval 结果
```

生产环境关闭 FastAPI 自带的 `/docs`、`/redoc`、`/openapi.json`，Caddy 同时返回 404。

---

## 9. EDGAR 数据管道

数据源（免费、无需 key）：

- ticker → CIK：`https://www.sec.gov/files/company_tickers.json`
- 公司申报列表：`https://data.sec.gov/submissions/CIK##########.json`
- XBRL 数据：`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

SEC 访问规定：每个请求带 `User-Agent: <名字> <邮箱>`（放在环境变量 `SEC_USER_AGENT`），速率不超过每秒 10 次。

v1 指标和概念回退顺序：

| metric | XBRL concept（按顺序尝试） |
|---|---|
| revenue | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `SalesRevenueNet` |
| gross_profit | `GrossProfit`；没有则 revenue − `CostOfRevenue` / `CostOfGoodsAndServicesSold`（标 derived） |
| operating_income | `OperatingIncomeLoss` |
| net_income | `NetIncomeLoss` |
| cfo | `NetCashProvidedByUsedInOperatingActivities` |
| capex | `PaymentsToAcquirePropertyPlantAndEquipment` |
| fcf | cfo − capex（derived） |

必须处理的坑：

1. **季度值 vs 累计值。** 10-Q 的现金流量表是年初至今累计（3/6/9 个月）。季度值要做差：Q2 = 6M − 3M，Q3 = 9M − 6M，Q4 = 全年（10-K）− 9M。按 `period_start` 到 `period_end` 的天数判断期间长度，不能只看 `fp`。所有做差得到的值标 `derived=true` 并记录公式，UI 上显示。
2. **财年不是自然年。** 例如 Micron 的财年约在 9 月结束。按 `period_end` 排序，展示时同时标财年标签。
3. **同一期间多次申报。** 后续文件会重复报告前期数字（包括重述）。展示用最新申报值，所有版本连同 accession 保留。
4. **不支持的公司。** 银行、保险等科目结构不同的公司，v1 标记 `supported=false` 并在 UI 里明确说明。

代码位置：纯提取逻辑在 `investment_core/financials.py`（输入 companyfacts JSON，输出季度指标和证据，不做 IO）；请求、限速和缓存在 `investment_data/edgar.py`；命令行 `python -m investment_data financials <TICKER>`。缓存目录 `data-cache/`（已 gitignore）。

网络：Cowork 的云端环境和本机 Linux 虚拟环境都无法访问 sec.gov（组织网络策略拦截），真实数据在 Windows 本机或服务器上运行。

覆盖范围：v1 维护一个约 30 家大型公司的清单（config），每日任务检查 submissions，有新的 10-Q/10-K 才重新拉 companyfacts。companyfacts 原始 JSON 较大，只存提取后的 facts。

黄金测试：用 `python -m investment_data record-fixture <TICKER> --out tests/fixtures/edgar` 录制 GOOG、TSLA、MSFT、MU 的精简 companyfacts。测试分两类：(1) 每个完整财年的四个季度之和等于年报数字；(2) 抽取几个季度与公司财报新闻稿人工核对，写进 `EXPECTED`。没有录制文件时这些测试自动跳过。

---

### 9.1 基本面与文字证据（计划，2026-09-28）

目前证据包只有财务数字。业务层面的信息（Waymo、TPU、云业务、搜索份额、诉讼）需要另外的来源，按可信度分三级：

| 级别 | 来源 | 例子 | 在 memo 里的地位 |
|---|---|---|---|
| 一级 | 公司向 SEC 提交的文件原文 | 10-K 第 1 项"业务"、第 1A 项"风险因素"、第 7 项"管理层讨论"、10-Q 分部附注、8-K 附带的财报新闻稿 | 可以作为"事实"，必须附原文引用 |
| 二级 | 公司自己发布、但不在 SEC 的材料 | 财报电话会议记录、投资者日、Waymo 官方博客里的运营数据 | 可以作为"事实"，但要附链接，并标明不是经审计的申报文件 |
| 三级 | 媒体、研究机构、社交媒体 | 市场份额估算、行业报告 | 只能是"待验证" |

实现方式：
- **分部数字**（例如 Google Services / Google Cloud / Other Bets 的收入和营业利润）：来自 10-Q/10-K 的分部附注。companyfacts 接口不含分部（维度）数据，需要解析申报文件本身的 XBRL。注意：Waymo 属于 Other Bets，公司不单独披露它的财务数字；TPU 也没有单独的数字，只在文字里出现。
- **文字证据**：抓取 10-K/10-Q 的相关章节，切成段落，每段有编号（申报号 + 章节 + 段落）。模型引用时必须给出原文片段，代码逐字核对该片段确实出现在对应段落中，对不上就拦下。这和数字 grounding 是同一个思路。
- **用户补充来源**：用户可以给某条论据附一个链接（例如 Waymo 博客），系统记录链接和日期，标为二级来源；AI 不能自己上网找。

实现状态（2026-09-28）：
- 分部与产品线数字：`investment_core/segments.py` 读取申报文件的 XBRL 实例（`*_htm.xml`），取 `StatementBusinessSegmentsAxis` 上的分部收入和营业利润、`ProductOrServiceAxis` 上的产品线收入，复用季度推导逻辑；最近 5 份 10-Q/10-K 足以推出第四季度。证据包加入分部收入、分部营业利润、分部营业利润率及同比。已与 Alphabet Q2 2026 财报核对（Google Cloud 收入 $24.77B、同比 +81.8%、营业利润 $8.81B）。
- 文字证据：`investment_core/filing_text.py` 把最新 10-K 的第 1、1A、7 项和最新 10-Q 的第 2 项切成段落（跳过表格和隐藏的 XBRL 头），按关键词做 BM25 检索，每个关键词取前 2 段并去重，默认最多 18 段。关键词 = 用户给的英文关键词 + 论点里的英文词 + 默认词（资本开支、折旧、竞争、AI、监管、营业亏损）。中文论点不会自动翻译成检索词，这是当前限制。
- 引用核对：claim 可以带 `quotes`（source_id + 原文），代码做规范化后的逐字子串匹配，长度 5–60 词。带引用的 fact 可以提到分部、产品和业务名称，数字可以来自引用原文。没有引用也没有分部数字支撑的 fact，出现 Waymo、TPU 这类专有名称会被拦下。
- 命令：`memo-draft ... --keywords "Waymo,TPU"`；`--no-fundamentals` 只用公司总数。

### 9.2 13F 机构持仓（Phase 4b，2026-09-28 完成第一版）

- 纯逻辑在 `investment_core/thirteenf.py`：解析信息表（2023-01-03 之前申报的 value 单位是千美元，之后是美元）、按 CUSIP 和 put/call 合并多行、按股数比较两个季度（新进、清仓、增持、减持）、集中度（前 1/5/10 大）。
- 取数在 `investment_data/thirteenf.py`：每个报告期取原始 13F-HR，暂不合并修正版（13F-HR/A）。
- 命令：`python -m investment_data 13f BRK-B`。黄金测试用 Berkshire 2026 Q1、Q2 两期原始文件，并与媒体报道核对（新进 D.R. Horton、清仓 Constellation Brands、增持 Alphabet 和 Delta、减持美国银行）。
- 局限（页面上要写明）：只有美股多头，季度结束后最多 45 天才披露，不含空头、现金、债券、非美资产；按股数比较，市值变化还受股价影响。
- 下一步：按发行人合并 A/C 等不同股份类别；多家机构对比；CUSIP 对应股票代码。

## 10. LLM 层

### 10.1 任务

| task | 用在哪里 | 对应 SOP |
|---|---|---|
| `skeptic_review` | Lab 第 2 步；私有 memo Step 4 | memo SOP Step 4 |
| `memo_user_review` | 私有 memo Step 6 | memo SOP Step 6 |
| （v2）`thesis_retest` | 财报后重审 | 复盘 SOP §9 |

### 10.2 grounding 原则

- 输入 = 用户论点 + **证据包**（从 `financial_facts` 取出的事实列表，每条有 `fact_id`、指标、数值、单位、期间、来源文件）。
- v1 **不给 LLM 联网搜索**。证据包以外的信息只能出现在 `verify_questions` 里，不能作为事实陈述。
- 用户输入（thesis）视为数据，用分隔标记包裹；无论 prompt 里写了什么，输出都要经过校验。

### 10.3 `skeptic_review` 输出 schema

```json
{
  "thesis_restated": "string",
  "bear_case": [
    {"claim": "string", "type": "fact|inference", "evidence_refs": ["fact_id"],
     "breaks_assumption": "如果成立，论点的哪个假设失效"}
  ],
  "weakest_assumption": "string",
  "invalidation_conditions": [
    {"condition": "string", "observable_metric": "string", "threshold": "string|null"}
  ],
  "verify_questions": [{"question": "string", "where_to_check": "string"}],
  "numbers_used": [{"value": 0, "unit": "string", "fact_id": "string"}]
}
```

`bear_case` 恰好 3 条，`invalidation_conditions` 至少 3 条。

### 10.4 自动校验（validator）

每次输出都要检查：

1. 符合 JSON schema。
2. **数字 grounding**：从所有文本字段中抽取数字（处理 `$`、`%`、`B/M`、千分位），年份和季度编号除外。每个数字都必须出现在 `numbers_used` 中，且对应的 `fact_id` 存在于证据包、数值在四舍五入容差内一致。
3. `type=fact` 的条目至少有一个 `evidence_refs`。
4. **禁止内容**：中英文的买入/卖出/持有建议、目标价、"值得买"、仓位建议等（关键词表 + 规则）。
5. 失败时带着具体错误重试一次；仍然失败则**返回安全错误信息，不把未通过校验的内容展示给用户**。

校验结果写进 `ai_runs.validation`。

### 10.4.1 实现后的调整（2026-09-27）

- 第一个任务实现为 `research_skeptic`（合并 SOP Step 3 的 bull/bear 整理和 Step 4 的反方审查），schema 见 `investment_ai/validate.py` 的 `ResearchSkeptic`：`bull_case` 2–4 条、`bear_case` 恰好 3 条、`weakest_assumption`、`invalidation_suggestions` 3–5 条、`verify_questions` 2–6 条。去掉了 `numbers_used`，改为代码直接扫描文本中的数字。
- 数字 grounding 收紧：bull/bear 里的数字必须和这条自己的 `evidence_refs` 对上；其他字段里的数字必须和证据包的显示值逐字一致（如 "$119.80B"、"24.1%"）。原因：证据包里有上百个比率，"30%" 这种粗略数字按容差匹配时会碰巧命中。建议的阈值（threshold）和用户论点里自己写的数字不检查。
- 证据包额外提供同比、环比变化，以及资本开支占收入、资本开支占经营现金流、FCF 利润率，都由代码计算，避免模型自己算。
- 事实与解读分开：`fact` 只复述证据；解读放在 `why_it_matters`（一律算推断）。`fact` 里出现"表明/说明/导致/indicates…"或把公司总数归到 AI、云、搜索等分部，校验不通过。失效条件建议和待验证问题里的数字属于假设阈值，不做 grounding，只查建议类禁止词。
- 两种生成方式，走同一套校验和渲染：(1) API 调用（`memo-draft`），用于服务器上线后的 Lab 和定时任务；(2) 对话内生成（`memo-draft --from-json`）：用户在 Cowork 对话里让 Claude 按证据包写出 JSON，再由代码校验、记录（provider=`claude-session`，API 花费为 0）、渲染。本地开发期和用户自己的日常研究用 (2)，不需要用户手动跑命令。
- 本地阶段 `ai_runs` 记录写在 `private-data/ai_runs.jsonl`，月度预算从这里累计；上线后迁到数据库表。

### 10.4.2 Step 6、Step 7 与资产负债表（2026-09-28）

- 资产负债表：`financials.py` 读取时点数据（现金及等价物、短期投资、非流动长期债务、一年内到期债务），推导"现金与短期投资"和"净现金"。只有公司报告了非流动长期债务标签的日期才计算总债务；`LongTermDebt` 这类含义不一致的标签不用来猜（TSLA、MU 因此暂无总债务）。
- Step 6（`memo_user_review_v1`）：从 memo Markdown 解析用户填写的 §A–§D（`memo_parse.py`），模型逐项判断：§A 是否有漏洞、与论点是否一致；§B 每条是 驳倒 / 接受风险（必须写明边界）/ 未驳倒 / 答非所问；§C 是否可观测；§D 是否具体。代码强制：空回应一律未驳倒，有效失效条件少于 3 条一律"需要修改"，缺目标仓位或复盘日期直接标出。仓位检查不交给模型，由闸口 `evaluate_trade` 模拟加到目标仓位后给出。审查结果写回 memo，状态写入 `private-data/memo_status.yaml`（作为 `--context` 供规则引擎读取）。
- Step 7（`memo-finalize`）：用核心状态机依次走 skeptic_done → user_responded → reviewed → final。§A–§D 不完整时拒绝定稿并列出缺什么；审查有未解决问题时选 `eligible_for_gate` 必须写理由。
- 示例论点：`fixtures/example_theses.yaml`，按股票和通用模板提供，给没有明确观点的访客和测试使用。

### 10.5 运行参数和记录

- 通过 provider adapter 调用；模型名、单价写在配置里，不写死在代码中。temperature 0–0.2，使用结构化输出。
- prompt 放在 `backend/prompts/`，文件名带版本号，每次调用记录 `prompt_version`。
- 每次调用都写 `ai_runs`：输入哈希、输入、输出、校验结果、token、成本、耗时。

### 10.6 eval

`backend/evals/` 放不少于 30 个用例（ticker × thesis），其中包括对抗用例："直接告诉我该不该买"、在 thesis 里写 prompt 注入、要求给目标价、证据包里没有的数字等。

指标：`schema_valid_rate`、`grounding_pass_rate`、`forbidden_rate`（必须为 0）、对抗用例的正确拒绝率、平均成本、p50 延迟。

通过 CLI 离线运行，结果 JSON 由 `/api/lab/evals/latest` 提供，在 Lab 页面上公开展示。self_web 中现在还是 draft 的 Prompt Observatory，可以用这套 eval 作为真实内容。

---

## 11. 公开 Lab 体验

路由都在 `invest.jun-liang-lyu.com/lab` 下，v1 只做英文，结构上预留 zh。

1. **`/lab/company/:ticker` 财报快照。** 最近 8 个季度的收入、毛利率、营业利润率、净利润、FCF 图表。每个数字点开显示来源文件和期间，做差得到的数字显示公式。不调用 LLM。
2. **`/lab/thesis` 反方审查。** 访客写一句论点（≤280 字符）→ `skeptic_review` → 展示 3 条 bear case（含证据链接）、最脆弱假设、失效条件、需要核实的问题。页面明确说明这不是投资建议。
3. **`/lab/memo` memo 草稿。** 把第 1、2 步的结果和访客自己填写的回应（对应 §A–§D）组装成一份 SOP 格式的 Markdown，供下载。访客没填的部分保留空白，不让 AI 代填。
4. **`/lab/gate` 实盘前闸口。** 选择一个虚构组合（例如"集中科技股""平衡型""大量现金"），提交一笔拟交易，展示逐项检查结果。不调用 LLM。
5. **`/lab/evals`** 展示最新的 eval 指标。

示例论点（`fixtures/example_theses.yaml`）默认隐藏：第 2 步只显示空白输入框，下方有一个不显眼的入口"只想体验一下？用示例论点"，点开后才列出示例。原因是示例会影响访客自己的思考，SOP 要求 Step 1 由用户自己写。

公开 LLM 调用的限制（第 2 步）：

- 每个 IP 每天 3 次（IP 加每日轮换的 salt 做哈希后计数，不存明文 IP）。
- 预算：所有 LLM 调用（Lab 和私有）合计每月不超过 `LLM_MONTHLY_BUDGET_USD`（$5），其中 Lab 每天不超过 `LAB_DAILY_BUDGET_USD`（$0.50）。调用前按最大 token 估算成本，任一上限用完就暂停对应功能并显示提示。离线 eval 用单独的 key 和预算，不计入。
- 相同输入走缓存：key = (ticker, 证据版本, 规范化后的 thesis 哈希, prompt_version)。
- 不接入人机验证（如 Cloudflare Turnstile）。目前访问量很小，每 IP 限流加预算上限已经保证花费不会超标；出现刷接口的情况再加。
- 访客输入保留 30 天：每日任务把 30 天前 Lab 调用在 `ai_runs` 中的 `input`/`output` 清空，只保留 token、成本、校验结果等统计字段。页面上说明这一点。

虚构组合放在 `fixtures/demo_portfolios/`，净值都是 $10,000，持仓数量和价格仅为示意，页面标注"虚构组合，价格截至 YYYY-MM-DD"。Lab 使用单独的 `fixtures/rules/demo.yaml`（参数同 v0 草拟规则，但保留 `break` 级别，演示效果更清楚），和私有规则互不影响。

| 组合 | 构成 | 用来演示 |
|---|---|---|
| Index Core | VOO 70%、BND 10%、VXUS 10%、现金 10% | 规则全部通过；试着加一只个股，看闸口怎么检查 |
| Concentrated Tech | NVDA 25%、MSFT 25%、现金 50% | 单股上限、主题集中度、没有 memo 的持仓 |
| Cash-Heavy Starter | 现金 90%、COST 10%；V 在观察名单上 | 新开仓时的首仓金额上限、观察期不足 |

---

## 12. 私有 dashboard

路由在 `invest.jun-liang-lyu.com/app` 下：

- `/app`：总览。净值、现金占比、持仓表（两种口径的占比）、越线列表（按严重程度）、`UNCHECKED_TRADE`、到期待办。
- `/app/import`：上传持仓文件，显示解析预览，确认后导入。
- `/app/memos`：按状态分列的看板；`/app/memos/:id` 按 Step 1–7 分步填写和操作。
- `/app/gate`：基于真实最新快照的实盘前闸口，结果持久化。
- `/app/reviews`：生成和查看月度复盘报告。
- `/app/rules`：规则集版本历史；编辑草稿、激活。

---

## 13. 安全与隐私

- 私有部分只有一个用户。密码的 argon2 哈希放在环境变量 `ADMIN_PASSWORD_HASH`。登录接口限流（每 15 分钟 5 次）。
- session cookie：`HttpOnly; Secure; SameSite=Strict`，有效期 7 天，服务端签名。修改类请求还需带自定义请求头 `X-Requested-With`，作为 CSRF 的第二层防护。
- 不存储任何有交易权限的券商凭据。v1.5 的 IBKR Flex token 是只读的，只存在服务器的 `.env.production`（chmod 600）里。
- 应用日志不打印持仓、金额或 thesis 全文。
- `private-data/`、`.env*` 都要 gitignore；提交前检查。
- 公开接口不能读私有数据，由查询层强制并写测试覆盖（§3）。

---

## 14. 部署与运维

沿用 self_web `docs/DEPLOYMENT.md` 的做法：**所有构建都在本地完成，服务器只接收构建好的产物。** 操作步骤见 `docs/DEPLOY.md`。

### 14.1 第一版（2026-09-28 实现）：公开 Lab，无数据库、无 LLM

第一版只上线财报快照和交易前闸口，都不需要数据库和密钥，所以先不动 PostgreSQL。

- 文件：`deploy/api.Dockerfile`（python:3.11-slim，非 root，单 worker）、`deploy/requirements-api.txt`（固定版本）、根目录 `.dockerignore`（白名单）、`deploy/compose.invest.yaml`（叠加在 self_web 的 compose 上）、`deploy/invest.caddy`、`deploy/build-release.ps1`、`deploy/invest.env.example`。
- 网络：`investment-api` 只在新建的 `invest` 网络上（与 Caddy 共享，可出网访问 sec.gov），不在 self_web 的 `backend` 内部网络上，因此访问不到 PostgreSQL；不对主机开放端口。self_web 的 `backend` 网络是 `internal: true`，不能出网，所以不能把 API 放进去。
- 容器加固：`read_only`、`tmpfs /tmp`、`cap_drop: ALL`、`no-new-privileges`、内存上限 256M。SEC 缓存放在命名卷 `invest_edgar_cache`。
- Lab 的发布目录独立：`/opt/investment/releases/<id>`，`/opt/investment/current` 指向当前版本；Caddy 通过绝对路径挂载 `invest.caddy` 和 `site/`。这样 self_web 发布替换 `/opt/portfolio/current` 时不会带走 Lab 的文件，两边可以分别回滚。
- self_web 的 Caddyfile 末尾加一行 `import /etc/caddy/sites/*.caddy`；没有匹配文件时 Caddy 只记 warning（已用 Caddy 2.10.2 验证两种情况都是 valid）。
- `invest.caddy`：安全响应头（CSP 只允许同源、禁止被嵌入）；`/` 308 到 `/lab`；`/assets/*` 存在的文件缓存一年；其余路径返回 `index.html`（单页应用）；`/api/docs`、`/api/openapi.json` 返回 404。注意 `redir * /lab 308` 里的 `*` 不能省：省掉后 Caddy 把 `/lab` 当成路径匹配器，`/` 会返回空白 200（本地测试时发现）。
- 快照接口：冷加载加锁串行（单次峰值约 104 MB，防止并发超出 256M，也减少对 SEC 的请求）；SEC 不可用时有旧快照就返回旧快照，否则返回 503。
- 验证：`backend/tests/test_deploy.py` 检查镜像只复制公开代码和 demo fixture、`.dockerignore` 是白名单、依赖固定版本、API 无端口且不在数据库网络、Caddy 屏蔽文档路径；按 Dockerfile 的目录结构和环境变量在本机模拟运行，10 家公司快照全部 200；用真实 Caddy 加构建好的前端做了路由、响应头和 CSP 测试（浏览器控制台无报错）；用 `docker compose config` 验证与 self_web compose 合并后的结果。
- 已知限制：SEC companyfacts 接口可能滞后于申报（2026-09-28 时 Visa 2026-07-29 提交的 10-Q 还没有进入 companyfacts，所以 V 最新一季停在 FY2026 Q2）；COST 的 fiscal_label 为空（52/53 周财年），页面显示季度结束日期。

### 14.2 后续：数据库、私有接口、LLM（Phase 2 剩余部分，尚未实现）

私有 dashboard 和公开 LLM 反方上线时才需要以下内容。届时 API 需要同时加入 `backend` 网络（访问数据库）并保留 `invest` 网络（出网）。

#### 数据库初始化（一次性）

现有 postgres volume 已经有数据，`docker-entrypoint-initdb.d` 不会再执行。需要手动执行一次：

```sh
docker compose --env-file .env.production exec postgres \
  psql -U "$POSTGRES_USER" -d postgres -c "CREATE ROLE investment_app LOGIN PASSWORD '<from env>';"
# 然后：CREATE DATABASE investment OWNER investment_app;
#      REVOKE CONNECT ON DATABASE investment FROM PUBLIC;
```

具体 SQL 放在 `deploy/sql/001_init_investment_db.sql`，密码通过变量传入，不写进文件。

#### compose 新增服务

```yaml
investment-api:
  image: ${INVESTMENT_API_IMAGE:?}
  restart: unless-stopped
  environment:
    DATABASE_URL: postgresql+psycopg://investment_app:${INVESTMENT_DB_PASSWORD:?}@postgres:5432/investment
    SESSION_SECRET: ${INVESTMENT_SESSION_SECRET:?}
    ADMIN_PASSWORD_HASH: ${INVESTMENT_ADMIN_PASSWORD_HASH:?}
    LLM_API_KEY: ${INVESTMENT_LLM_API_KEY:-}
    SEC_USER_AGENT: ${SEC_USER_AGENT:?}
    LLM_MONTHLY_BUDGET_USD: ${LLM_MONTHLY_BUDGET_USD:-5}
    LAB_DAILY_BUDGET_USD: ${LAB_DAILY_BUDGET_USD:-0.5}
    ALLOWED_ORIGIN: https://invest.${DOMAIN}
  networks: [backend]
  depends_on:
    postgres: {condition: service_healthy}
  deploy:
    resources:
      reservations: {memory: 128M}
      limits: {memory: 256M}
```

容器启动时先执行 `alembic upgrade head` 再启动 uvicorn（单 worker），与 registry 在启动时跑 Flyway 的做法一致。已应用的迁移不能修改。

内存：现有三个容器的上限合计 1.28G（caddy 128M + registry 768M + postgres 384M），加上这个服务后是 1.54G，机器总内存 2GB。上线后用 `docker stats` 观察实际占用；建议给主机加 1GB swap 作为兜底。

#### Caddy 新增 site block

（已由 §14.1 的 `deploy/invest.caddy` 实现，下面是最初的草案。）

```
invest.{$DOMAIN} {
  encode zstd gzip
  @blocked path /docs* /redoc* /openapi.json
  respond @blocked 404
  handle /api/* {
    reverse_proxy investment-api:8081
  }
  handle {
    root * /srv/invest
    try_files {path} /index.html
    file_server
  }
}
```

caddy 服务增加挂载 `./invest-site:/srv/invest:ro`。先在 DNS 为 `invest` 添加 A 记录指向同一个静态 IP，Caddy 才能签发证书。

#### 定时任务

主机 cron 调用容器内命令，例如：

- 每日：`docker compose exec -T investment-api python -m investment_api.jobs edgar_refresh`
- 每日：`... jobs review_tasks`（生成月度复盘和财报待办）
- 每日：`... jobs purge_lab_inputs`（清空 30 天前的 Lab 访客输入）

#### 备份

现有 `backup-postgres.sh` 只 dump `$POSTGRES_DB`（registry 的库）。需要扩展为同时 dump `investment` 库，文件名分开；恢复流程同样在临时 Compose 项目中验证。

#### 发布检查

```sh
curl --fail https://invest.jun-liang-lyu.com/api/health
curl -o /dev/null -s -w '%{http_code}\n' https://invest.jun-liang-lyu.com/api/private/overview   # 期望 401
curl -o /dev/null -s -w '%{http_code}\n' https://invest.jun-liang-lyu.com/docs                   # 期望 404
# 以及 self_web DEPLOYMENT.md 中的原有检查全部仍然通过
```

---

## 15. 与 self_web、registry 的集成

- self_web 只加内容，不改代码：`content/lab/investment-lab.en.mdx`（之后加 `.zh.mdx`），`type: tool`，`visibility: [professional, creator]`，`demo: https://invest.jun-liang-lyu.com/lab`，仓库公开后再加 `github`。
- Phase 5 完成后，在 `content/projects/` 写完整案例：问题背景、架构、规则引擎和 LLM 分工、grounding 校验、eval 结果、取舍。
- registry：通过管理员接口登记一条服务，health URL 为 `/api/health`。
- 任何修改后，self_web 的 creator/professional 输出审计必须仍然通过。

---

## 16. 分阶段计划与验收标准

预计总共 5–6 周（非全职）。

**2026-09-27 调整顺序：先做研究线。** Phase 0/1 完成后，先做 Phase 4（财报管道）和 Phase 5（memo 生成、AI 反方），以本地命令行形式先用起来，结果暂存为文件；然后再做 Phase 2（后端、数据库、部署）和 Phase 3（Lab 上线），把所有功能一起搬到网站。原因：当前系统只有检查和报告，缺的是能产出内容的功能；代价是网站晚 2–3 周上线。

**新增功能（2026-09-27 确认要做）：**

- **Phase 4b：13F 机构持仓追踪。** 复用 EDGAR 管道，从 Berkshire 开始。用途是学习别人的配置方式，为规则定稿提供依据。局限要在页面上写清楚：13F 只包含美股多头仓位，季度结束后最多 45 天才披露，看不到空头、现金、债券和非美资产，只能反映配置结构，不能反映买卖时机。
- **Phase 5 之后、上网站之前：** 复盘提醒（月度复盘、memo 自带的复盘日期、财报发布后重审）；论点追踪（每次财报后对照失效条件记录论点是否成立）；组合与 VOO 的收益对比；成熟策略对比（本地 LEAN/Qlib 预先计算）。

### Phase 0：现状和规则整理（用户 + Claude，1–2 个晚上）

- 从 IBKR 导出当前持仓，放进 `private-data/`。
- 用 Claude 按组合复盘 SOP 跑一次复盘，作为基线。
- 决定规则 v1 的数值（可以之后再定，不阻塞开发）。

**验收**：`private-data/` 里有一份当前快照；v0 草拟规则写成 `fixtures/rules/v0_draft.yaml`。

### Phase 1：`investment_core`（约 1 周）

交付：模型、规则引擎、闸口、memo 状态机、复盘报告、CSV 和 IBKR 解析器、v0 导入器、CLI（`evaluate` / `gate` / `review`）。

**验收**（pytest 全部通过，`rules/` 和 `gate.py` 覆盖率 ≥ 90%）：

- 单股略低于上限的 fixture（虚构代码，净值 $10,000）：一只股票约 14% 且没有 memo → `MEMO_REQUIRED_ABOVE`；没有 Core → `CORE_TARGET_WEIGHT`；一笔小额未分类持仓 → `UNCLASSIFIED_POSITION`。
- 两股集中 fixture（虚构代码，两只个股合计约 50%，其余现金，两者 exposure tag 相同）：报出 `SATELLITE_MAX_WEIGHT`、`EXPOSURE_MAX_WEIGHT`、`CORE_TARGET_WEIGHT`、`TOP3_MAX_WEIGHT_INVESTED`。
- 闸口：某持仓已占 14%，拟买入后超过 15% → `SINGLE_MAX_WEIGHT_NAV` fail；没有 memo → memo 项 fail；self_attest 项默认 `unknown`。
- 两次快照做差：持仓增加且之前 7 天内没有闸口记录 → `UNCHECKED_TRADE`。
- memo 非法状态转换抛异常；带 Override 的转换写入理由。
- GICS 行业不同但 exposure tag 相同的两只股票，集中度按 tag 合并计算。

### Phase 2：API + 数据库 + 私有 dashboard + 部署（约 1 周）

交付：FastAPI、Alembic 迁移、认证、私有接口、导入上传；dashboard 的总览、导入、memo 列表、规则（只读）页面；compose 和 Caddy 修改、数据库初始化、备份扩展；建好 `ai_runs` 表。

**验收**：§14.6 的检查全部符合预期；导入真实文件后总览页显示越线；备份包含 investment 库，并在临时项目中完成一次恢复；`docker stats` 显示 investment-api 占用低于 256M；主站和 registry 的原有检查不受影响。

### Phase 3：Lab 第 4 步（闸口）+ self_web 上架（2–3 天）

**验收**：`/lab/gate` 可以使用 3 个虚构组合；无需登录；有限流；self_web 的 Lab 卡片上线；registry 已登记；self_web 两种模式的审计都通过。

### Phase 4：EDGAR 管道 + Lab 第 1 步（约 1 周）

**验收**：约 30 家公司的清单每日刷新；GOOGL/TSLA/MSFT/MU 黄金测试通过；derived 数值带公式；每个数字能链接到来源文件；不支持的公司有明确说明；请求带 User-Agent 且速率不超过每秒 10 次。

### Phase 5：LLM——Lab 第 2、3 步 + 私有 Step 4/6（约 1–1.5 周）

**验收**：eval 用例 ≥ 30；`forbidden_rate` = 0；`schema_valid_rate` ≥ 98%；重试后 `grounding_pass_rate` ≥ 95%；把预算上限设得极小时能触发暂停（日上限和月上限都要测）；30 天清理任务有测试；每次调用都写入 `ai_runs` 并带成本；`/lab/evals` 页面上线；下载的 memo 草稿标题结构与 memo SOP 一致。

### 之后（v2+）

IBKR Flex 自动同步；Thesis 记分卡；Berkshire 等机构 13F 追踪（复用 EDGAR 管道）；成熟策略和历史场景 Lab（本地 LEAN/Qlib 预先计算）。这两项既是展示内容，也是参考别人配置方案、为规则定稿提供依据的工具；中文 UI；邮件提醒。

---

## 17. 已定事项与待决问题

### 已定（2026-09-27）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 规则 v1 数值 | 暂不定稿。当前以小仓位为主，先参考更多别人的配置方案再定；私有模式下 v0 草拟规则以 warn/info 运行（§6.2）。 |
| 2 | 应用地址 | `invest.jun-liang-lyu.com`，访客从 self_web 的 Lab 条目进入（§4）。 |
| 3 | 仓库是否公开 | 公开；个人内容不进仓库（§3）。 |
| 4 | LLM 预算 | API 每月合计 ≤ $5，Lab 每天 ≤ $0.50（§11）。API 额度主要给公开 Lab；用户自己的研究以 Claude 订阅套餐为主。 |
| 5 | 访客论点保留 | 30 天后删除（§11）。 |
| 6 | 人机验证 | 暂不接入，出现滥用再加（§11）。 |
| 7 | 虚构组合 | 三个：Index Core、Concentrated Tech、Cash-Heavy Starter（§11）。 |
| 8 | LLM 提供方 | 只用 Anthropic（Claude，已充值 $5），私有内容和公开 Lab 都走它。Gemini 的适配代码保留但不启用（用户的 Google 项目是付费档且需要预付）。密钥放在 `investment-lab/.env`（已 gitignore），模板见 `.env.example`。 |

### 仍待决

- 具体模型型号（Phase 5 开始时按价格和效果定）。
- 规则 v1 定稿时间（不阻塞开发）。

---

## 18. 参考

- `../personal_investment_system_v0/个人投资规则_v0.md`
- `../personal_investment_system_v0/investment_memo_SOP_v0.md`
- `../personal_investment_system_v0/实盘前检查清单.md`
- `../personal_investment_system_v0/组合复盘_SOP.md`
- `../personal_investment_system_v0/持仓登记表.md`
- `../personal_investment_system_v0/继续工作记录.md`（§0c 架构方向、§0d Reference Layer）
- self_web：`docs/DEPLOYMENT.md`、`docs/HANDOFF.md`、`deploy/compose.yaml`、`deploy/Caddyfile`
