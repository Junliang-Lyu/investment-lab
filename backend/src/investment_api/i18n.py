"""Chinese display text for the public Lab (DESIGN §11). The rule engine speaks English; the API
translates its fixed labels and message templates here so the core stays free of UI concerns.
Unknown text is returned unchanged (tests make sure every template the demo can produce is covered)."""

from __future__ import annotations

import re

SECTIONS = {
    "§1 basics": "§1 基本条件", "§4 investment memo": "§4 投资 memo", "§5 skeptic review": "§5 反方审查",
    "§6 position & concentration": "§6 仓位与集中度", "§7 paper to live": "§7 模拟到实盘",
    "§8 exit & invalidation": "§8 退出与失效条件", "§10 final gate": "§10 最终确认",
}

STATIC = {
    "Enough cash (margin is not modeled)": "现金足够（不考虑融资）",
    "Memo exists and has been reviewed": "已有 memo 且经过审查",
    "Three strongest counter-arguments recorded": "已记录最强的三条反方意见",
    "Memo conclusion allows the pre-trade gate": "memo 结论允许进入交易前闸口",
    "Paper-to-live checks": "模拟到实盘检查",
    "No memo for this symbol": "这只标的没有 memo",
    "Not on paper/watchlist": "不在模拟/观察名单上",
    "Within limit after this trade": "交易后仍在限额内",
    "Core position: covered by the core strategy SOP, not the memo SOP": "核心仓位：由核心策略 SOP 管理，不适用 memo SOP",
    "Already held live; applies to new positions only": "已经实盘持有；只适用于新开仓",
    "No portfolio review on record": "没有组合复盘记录",
    # self-attestations
    "Not driven by a short-term price rise (FOMO)": "不是因为短期上涨才想买（FOMO）",
    "Not buying mainly to lower the average cost of a losing position": "买入不是主要为了摊低亏损仓位的成本",
    "A 20-30% drop in this position would not affect my life or my discipline": "这个仓位下跌 20–30% 不会影响我的生活和纪律",
    "I know what would prove this thesis wrong": "我知道什么情况能证明这个论点是错的",
    "This is my decision, not one made by AI": "这是我自己的决定，不是 AI 替我做的",
    "Selling because of the thesis or its invalidation conditions, not short-term price moves":
        "卖出是因为论点或失效条件，而不是短期价格波动",
}

PCT = r"(-?[\d.]+%)"
USD = r"(\$[\d,]+)"
TEMPLATES: list[tuple[re.Pattern, str]] = [(re.compile(p), r) for p, r in [
    # gate labels and details
    (r"^At least (\d+) invalidation conditions$", r"至少 \1 条失效条件"),
    (r"^Tracked on paper/watchlist for at least (\d+) weeks$", r"在模拟/观察名单上跟踪至少 \1 周"),
    (r"^At least (\d+) reviews while on paper/watchlist$", r"模拟/观察期间至少复盘 \1 次"),
    (rf"^First live position no larger than {USD}$", r"首次实盘仓位不超过 \1"),
    (rf"^Cash {USD}, trade {USD}$", r"现金 \1，交易 \2"),
    (r"^Memo status '(\w+)', required '(\w+)'$", r"memo 状态：\1，要求：\2"),
    (r"^Memo status '(\w+)'$", r"memo 状态：\1"),
    (r"^Memo decision: (\w+)$", r"memo 结论：\1"),
    (r"^(\d+) recorded$", r"已记录 \1 条"),
    (r"^([\d.]+) weeks$", r"\1 周"),
    (r"^(\d+) review\(s\)$", r"\1 次复盘"),
    (rf"^Proposed {USD}$", r"拟投入 \1"),
    # rule engine messages
    (rf"^Core is {PCT} of net value, target {PCT}$", r"核心仓位占净值 \1，目标 \2"),
    (rf"^Satellite is {PCT} of net value, limit {PCT}$", r"卫星仓位占净值 \1，上限 \2"),
    (rf"^(\S+) is {PCT} of net value, limit {PCT}$", r"\1 占净值 \2，上限 \3"),
    (rf"^(\S+) is {PCT} of net value with no memo \(required above {PCT}\)$", r"\1 占净值 \2，但没有 memo（超过 \3 必须有）"),
    (rf"^(\S+) is {PCT} of net value; memo is only at '(\w+)', needs '(\w+)'$", r"\1 占净值 \2；memo 只到 \3 阶段，需要 \4"),
    (rf"^Exposure '([\w-]+)' is {PCT} of net value, limit {PCT}$", r"主题「\1」占净值 \2，上限 \3"),
    (rf"^(\S+) is {PCT} of invested capital, limit {PCT}$", r"\1 占已投入资金 \2，上限 \3"),
    (rf"^Top holdings \((.+)\) are {PCT} of invested capital, limit {PCT}$", r"前几大持仓（\1）占已投入资金 \2，上限 \3"),
    (rf"^(\S+) unrealized loss {USD} reached review trigger {USD}$", r"\1 浮亏 \2，达到复盘触发线 \3"),
    (r"^Last portfolio review was (\d+) days ago \(every (\d+) days\)$", r"上次组合复盘在 \1 天前（要求每 \2 天一次）"),
    (r"^(\S+) is not classified as core or satellite$", r"\1 还没有归类为核心或卫星仓位"),
    (r"^(\S+) has (\d+) invalidation condition\(s\), needs (\d+)$", r"\1 有 \2 条失效条件，需要 \3 条"),
]]
NOT_WORSE = re.compile(r"^Not made worse by this trade \(already over limit: (.*)\)$")


def zh(text: str | None) -> str | None:
    if not text:
        return text
    if text in STATIC:
        return STATIC[text]
    if text in SECTIONS:
        return SECTIONS[text]
    m = NOT_WORSE.match(text)
    if m:
        return f"这笔交易没有让情况变差（原本已超限：{zh(m.group(1))}）"
    if "; " in text:
        return "；".join(zh(part) for part in text.split("; "))
    for pattern, repl in TEMPLATES:
        if pattern.match(text):
            return pattern.sub(repl, text)
    return text


def translate_gate(result: dict) -> dict:
    for item in result.get("items", []):
        item["section"] = SECTIONS.get(item["section"], item["section"])
        if item.get("kind") == "self_attest" or not item.get("label", "").isupper():
            item["label"] = zh(item["label"])  # rule-code labels (SATELLITE_MAX_WEIGHT) are named by the page
        item["detail"] = zh(item.get("detail"))
    return result


def translate_findings(findings: list[dict]) -> list[dict]:
    return [{**f, "message": zh(f["message"])} for f in findings]
