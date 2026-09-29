import { useEffect, useMemo, useState } from "react";
import { api, CustomPortfolio, DemoPortfolio, GateResult } from "../api";
import { pct, usd } from "../format";
import { useLang } from "../i18n";
import { savedMemos } from "../memos";
import { navigate } from "../App";

const HOLDINGS = "lab-holdings";
const EMPTY: CustomPortfolio = { cash: 5000, positions: [{ symbol: "VOO", market_value: 5000, sleeve: "core" }] };

function loadHoldings(): CustomPortfolio {
  try {
    const v = JSON.parse(localStorage.getItem(HOLDINGS) ?? "null");
    if (v && typeof v.cash === "number" && Array.isArray(v.positions)) return v;
  } catch { /* storage may be blocked */ }
  return EMPTY;
}

const sectionNo = (s: string) => Number(/§(\d+)/.exec(s)?.[1] ?? 99);
const OVERALL_CLS: Record<GateResult["overall"], string> = { clear: "ok", incomplete: "info", warnings: "warn", rule_breaks: "bad" };

export default function Gate() {
  const { t, lang } = useLang();
  const params = new URLSearchParams(window.location.search);
  const [portfolios, setPortfolios] = useState<DemoPortfolio[]>([]);
  const [pid, setPid] = useState(params.get("memo") ? "cash-heavy-starter" : "concentrated-tech");
  const [symbol, setSymbol] = useState(params.get("symbol")?.toUpperCase() || "AMZN");
  const [memos] = useState(savedMemos);
  const [memoId, setMemoId] = useState(params.get("memo") ?? "");
  const [holdings, setHoldings] = useState<CustomPortfolio>(loadHoldings);
  const [side, setSide] = useState<"buy" | "sell">(params.get("side") === "sell" ? "sell" : "buy");
  const [amount, setAmount] = useState(800);
  const [attest, setAttest] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<GateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.demoPortfolios(lang).then(setPortfolios).catch((e) => setError(String(e.message ?? e))); }, [lang]);
  const portfolio = useMemo(() => portfolios.find((p) => p.id === pid), [portfolios, pid]);
  const customNav = holdings.cash + holdings.positions.reduce((a, p) => a + (p.market_value || 0), 0);
  const memoChoices = memos.filter((m) => m.ticker === symbol || m.id === memoId);
  useEffect(() => { try { localStorage.setItem(HOLDINGS, JSON.stringify(holdings)); } catch { /* ignore */ } }, [holdings]);
  const setRow = (i: number, patch: Partial<CustomPortfolio["positions"][number]>) => {
    setHoldings({ ...holdings, positions: holdings.positions.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
    setResult(null);
  };

  async function run(nextAttest = attest) {
    setError(null);
    try {
      setResult(await api.gate(lang, {
        portfolio_id: pid, symbol, side, amount_usd: amount, attestations: nextAttest,
        ...(memoId ? { memo_id: memoId } : {}),
        ...(pid === "custom" ? { custom: { cash: holdings.cash, positions: holdings.positions.filter((p) => p.symbol && p.market_value > 0) } } : {}),
      }));
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
      <h1>{t.gateTitle}</h1>
      <p className="lede">{t.gateLede}</p>

      <div className="cards three">
        {portfolios.map((p) => (
          <button key={p.id} className={`card ${p.id === pid ? "selected" : ""}`} onClick={() => { setPid(p.id); setResult(null); }}>
            <h2>{p.name}</h2>
            <p className="small">{p.description}</p>
            <ul className="positions">
              {p.positions.map((x) => <li key={x.symbol}><span>{x.symbol}</span><span>{pct(x.weight)}</span></li>)}
              <li className="muted"><span>{t.cash}</span><span>{pct(p.cash / p.net_liquidation)}</span></li>
            </ul>
            <p className="small muted">{p.findings.length === 0 ? t.noFindings : t.findings(p.findings.length)}</p>
          </button>
        ))}
        <button className={`card ${pid === "custom" ? "selected" : ""}`} onClick={() => { setPid("custom"); setResult(null); }}>
          <h2>{t.gtCustom}</h2>
          <p className="small">{t.gtCustomNote}</p>
          <ul className="positions">
            {holdings.positions.filter((x) => x.symbol).map((x, i) => (
              <li key={i}><span>{x.symbol.toUpperCase()}</span><span>{customNav > 0 ? pct(x.market_value / customNav) : "–"}</span></li>
            ))}
            <li className="muted"><span>{t.cash}</span><span>{customNav > 0 ? pct(holdings.cash / customNav) : "–"}</span></li>
          </ul>
        </button>
      </div>

      {pid === "custom" && (
        <div className="holdings">
          <table>
            <thead><tr><th>{t.symbol}</th><th>{t.gtValue}</th><th>{t.gtType}</th><th></th></tr></thead>
            <tbody>
              {holdings.positions.map((p, i) => (
                <tr key={i}>
                  <td><input value={p.symbol} maxLength={10} onChange={(e) => setRow(i, { symbol: e.target.value.toUpperCase() })} /></td>
                  <td><input type="number" min={0} step="any" value={p.market_value} onChange={(e) => setRow(i, { market_value: Number(e.target.value) })} /></td>
                  <td>
                    <select value={p.sleeve} onChange={(e) => setRow(i, { sleeve: e.target.value as "core" | "satellite" })}>
                      <option value="core">{t.gtCore}</option><option value="satellite">{t.gtSatellite}</option>
                    </select>
                  </td>
                  <td><button type="button" className="linkish" onClick={() => { setHoldings({ ...holdings, positions: holdings.positions.filter((_, j) => j !== i) }); setResult(null); }}>{t.gtRemove}</button></td>
                </tr>
              ))}
              <tr>
                <th>{t.cash}</th>
                <td><input type="number" min={0} step="any" value={holdings.cash} onChange={(e) => { setHoldings({ ...holdings, cash: Number(e.target.value) }); setResult(null); }} /></td>
                <td colSpan={2}>
                  {holdings.positions.length < 40 && (
                    <button type="button" className="linkish" onClick={() => setHoldings({ ...holdings, positions: [...holdings.positions, { symbol: "", market_value: 0, sleeve: "satellite" }] })}>+ {t.gtAddRow}</button>
                  )}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {(portfolio || pid === "custom") && (
        <form className="trade" onSubmit={(e) => { e.preventDefault(); setAttest({}); run({}); }}>
          <label>{t.side} <select value={side} onChange={(e) => setSide(e.target.value as "buy" | "sell")}><option value="buy">{t.buy}</option><option value="sell">{t.sell}</option></select></label>
          <label>{t.symbol} <input value={symbol} maxLength={10} onChange={(e) => { setSymbol(e.target.value.toUpperCase()); setMemoId(""); }} /></label>
          <label>{t.amount} <input type="number" min={1} step="any" value={amount} onChange={(e) => setAmount(Number(e.target.value))} /></label>
          <label>{t.gtMemo}
            <select value={memoId} onChange={(e) => setMemoId(e.target.value)}>
              <option value="">{t.gtNoMemo}</option>
              {memoId && !memoChoices.some((m) => m.id === memoId) && <option value={memoId}>{symbol} memo</option>}
              {memoChoices.map((m) => <option key={m.id} value={m.id}>{m.ticker} · {m.thesis.slice(0, 30)}</option>)}
            </select>
          </label>
          {memoId && (
            <a href={`/lab/memo/${memoId}`} className="small"
               onClick={(e) => { e.preventDefault(); navigate(`/lab/memo/${memoId}`); }}>{t.gtOpenMemo} →</a>
          )}
          <button type="submit">{t.runGate}</button>
          {portfolio && <span className="muted small">{t.netValue(usd(portfolio.net_liquidation), portfolio.price_date, portfolio.rule_set)}</span>}
        </form>
      )}

      {error && <p className="error">{error}</p>}
      {result && (
        <div className="result">
          <div className={`overall ${OVERALL_CLS[result.overall]}`}>
            {t.overall[result.overall]}
            <span className="small"> — {result.side === "sell" ? t.sell : t.buy} {result.symbol} {usd(result.amount_usd)}: {pct(result.weight_before)} → {pct(result.weight_after)} {t.ofNet}</span>
          </div>
          {result.memo_status && <p className="muted small">{t.gtMemoUsing(result.memo_status)}</p>}
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
                        <span className={`badge ${i.status}`}>{t.status[i.status]}</span>
                        <span className={`sev ${i.severity}`}>{t.severity[i.severity]}</span>
                        <span className="label">{t.rules[i.label] ?? i.label}</span>
                        {t.rules[i.label] && <span className="rulecode">{i.label}</span>}
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
