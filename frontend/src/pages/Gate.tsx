import { useEffect, useMemo, useState } from "react";
import { api, DemoPortfolio, GateResult } from "../api";
import { pct, usd } from "../format";

const RULE_LABEL: Record<string, string> = {
  SINGLE_MAX_WEIGHT_NAV: "Single position within limit (share of net value)",
  SINGLE_MAX_WEIGHT_INVESTED: "Single position within limit (share of invested capital)",
  TOP3_MAX_WEIGHT_INVESTED: "Top holdings within limit (share of invested capital)",
  EXPOSURE_MAX_WEIGHT: "Theme exposure within limit",
  SATELLITE_MAX_WEIGHT: "Satellite sleeve within limit",
  MEMO_REQUIRED_ABOVE: "Memo required above size threshold",
};
const SEVERITY = { break: "hard rule", warn: "soft rule", info: "info" } as const;
const sectionNo = (s: string) => Number(/§(\d+)/.exec(s)?.[1] ?? 99);

const OVERALL: Record<GateResult["overall"], { text: string; cls: string }> = {
  clear: { text: "Clear: every check passed", cls: "ok" },
  incomplete: { text: "Incomplete: answer the self-check questions", cls: "info" },
  warnings: { text: "Warnings: some checks failed", cls: "warn" },
  rule_breaks: { text: "Rule breaks: hard limits failed", cls: "bad" },
};

export default function Gate() {
  const [portfolios, setPortfolios] = useState<DemoPortfolio[]>([]);
  const [pid, setPid] = useState("concentrated-tech");
  const [symbol, setSymbol] = useState("AMZN");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [amount, setAmount] = useState(800);
  const [attest, setAttest] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<GateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.demoPortfolios().then(setPortfolios).catch((e) => setError(String(e.message ?? e))); }, []);
  const portfolio = useMemo(() => portfolios.find((p) => p.id === pid), [portfolios, pid]);

  async function run(nextAttest = attest) {
    setError(null);
    try {
      setResult(await api.gate({ portfolio_id: pid, symbol, side, amount_usd: amount, attestations: nextAttest }));
    } catch (e) { setResult(null); setError(String((e as Error).message ?? e)); }
  }
  function toggle(key: string, value: boolean) {
    const next = { ...attest, [key.replace(/^attest:/, "")]: value };
    setAttest(next);
    if (result) run(next);
  }

  const sections = useMemo(() => {
    const m = new Map<string, GateResult["items"]>();
    (result?.items ?? []).forEach((i) => m.set(i.section, [...(m.get(i.section) ?? []), i]));
    return [...m.entries()].sort((a, b) => sectionNo(a[0]) - sectionNo(b[0]));
  }, [result]);

  return (
    <section>
      <h1>Pre-trade gate</h1>
      <p className="lede">The gate never says “buy”. It simulates the trade, re-runs the portfolio rules, and walks the checklist. Portfolios below are fictional.</p>

      <div className="cards three">
        {portfolios.map((p) => (
          <button key={p.id} className={`card ${p.id === pid ? "selected" : ""}`} onClick={() => { setPid(p.id); setResult(null); }}>
            <h2>{p.name}</h2>
            <p className="small">{p.description}</p>
            <ul className="positions">
              {p.positions.map((x) => <li key={x.symbol}><span>{x.symbol}</span><span>{pct(x.weight)}</span></li>)}
              <li className="muted"><span>Cash</span><span>{pct(p.cash / p.net_liquidation)}</span></li>
            </ul>
            <p className="small muted">{p.findings.length === 0 ? "No rule findings" : `${p.findings.length} rule finding(s) today`}</p>
          </button>
        ))}
      </div>

      {portfolio && (
        <form className="trade" onSubmit={(e) => { e.preventDefault(); setAttest({}); run({}); }}>
          <label>Side <select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")}><option>buy</option><option>sell</option></select></label>
          <label>Symbol <input value={symbol} maxLength={10} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></label>
          <label>Amount (USD) <input type="number" min={1} step="any" value={amount} onChange={(e) => setAmount(Number(e.target.value))} /></label>
          <button type="submit">Run the gate</button>
          <span className="muted small">Net value {usd(portfolio.net_liquidation)} · prices as of {portfolio.price_date} (illustrative) · rules {portfolio.rule_set}</span>
        </form>
      )}

      {error && <p className="error">{error}</p>}
      {result && (
        <div className="result">
          <div className={`overall ${OVERALL[result.overall].cls}`}>
            {OVERALL[result.overall].text}
            <span className="small"> — {result.side} {result.symbol} {usd(result.amount_usd)}: {pct(result.weight_before)} → {pct(result.weight_after)} of net value</span>
          </div>
          {sections.map(([section, items]) => (
            <div key={section} className="section">
              <h3>{section}</h3>
              <ul className="checks">
                {items.map((i) => (
                  <li key={i.key} className={i.status}>
                    {i.kind === "self_attest" ? (
                      <label className="attest">
                        <input type="checkbox" checked={i.status === "pass"} onChange={(e) => toggle(i.key, e.target.checked)} />
                        {i.label}
                      </label>
                    ) : (
                      <>
                        <span className={`badge ${i.status}`}>{i.status.replace("_", " ")}</span>
                        <span className={`sev ${i.severity}`}>{SEVERITY[i.severity]}</span>
                        <span className="label">{RULE_LABEL[i.label] ?? i.label}</span>
                        {RULE_LABEL[i.label] && <span className="rulecode">{i.label}</span>}
                        {i.detail && <div className="detail">{i.detail}</div>}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
