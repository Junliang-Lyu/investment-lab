import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, Snapshot } from "../api";
import { metric, pct, usd } from "../format";
import { navigate } from "../App";
import { useLang } from "../i18n";

const ROWS = ["revenue", "gross_margin", "operating_income", "operating_margin", "net_income", "cfo", "capex", "fcf",
  "cash_and_investments", "net_cash"];

// Margins beyond ±100% (tiny revenue, large losses) are shown as not meaningful.
function segmentMargin(rev?: number, oi?: number): string {
  if (rev === undefined || oi === undefined || rev <= 0) return "–";
  const m = oi / rev;
  return Math.abs(m) > 1 ? "n/m" : pct(m);
}

export default function Company() {
  const { t, lang } = useLang();
  const [tickers, setTickers] = useState<string[]>([]);
  const [ticker, setTicker] = useState("GOOG");
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [other, setOther] = useState("");
  const curated = tickers.length === 0 || tickers.includes(ticker);
  const loadOther = (e: FormEvent) => {
    e.preventDefault();
    const v = other.trim().toUpperCase().replace(".", "-");
    if (/^[A-Z][A-Z\-]{0,9}$/.test(v)) { setTicker(v); setOther(""); }
  };

  useEffect(() => { api.companies().then((c) => setTickers(c.map((x) => x.ticker))).catch(() => setTickers(["GOOG"])); }, []);
  useEffect(() => {
    setLoading(true); setError(null);
    api.snapshot(ticker, lang).then(setData).catch((e) => { setData(null); setError(String(e.message ?? e)); })
      .finally(() => setLoading(false));
  }, [ticker, lang]);

  const scroller = useRef<HTMLDivElement>(null);
  // Newest quarter is on the right; start there so narrow screens see the latest numbers first.
  useEffect(() => { const el = scroller.current; if (el) el.scrollLeft = el.scrollWidth; }, [data]);
  const quarters = data?.quarters ?? [];
  const segments = useMemo(() => {
    const by = new Map<string, Record<string, Snapshot["segments"][number]>>();
    (data?.segments ?? []).forEach((s) => by.set(s.member, { ...(by.get(s.member) ?? {}), [s.metric]: s }));
    return [...by.entries()].sort((a, b) => (b[1].segment_revenue?.value ?? 0) - (a[1].segment_revenue?.value ?? 0));
  }, [data]);
  return (
    <section>
      <h1>{t.snapTitle}</h1>
      <p className="lede">{t.snapLede}</p>
      <div className="toolbar">
        <label>{t.company}{" "}
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {[...tickers, ...(curated ? [] : [ticker])].map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <form className="inline" onSubmit={loadOther}>
          <input value={other} onChange={(e) => setOther(e.target.value)} placeholder={t.customPlaceholder}
                 aria-label={t.customLabel} maxLength={10} size={12} />{" "}
          <button type="submit" className="secondary">{t.customGo}</button>
        </form>
        {data && <span className="muted">{data.company} · CIK {data.cik}</span>}
        {curated && <button type="button" className="secondary" onClick={() => navigate("/lab/skeptic", { ticker })}>{t.snapNext(ticker)} →</button>}
      </div>
      {!curated && <p className="muted small">{t.customNote(ticker)}</p>}
      {loading && <p className="muted">{t.loadingFilings}</p>}
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <div className="scroll" ref={scroller}>
            <table>
              <thead>
                <tr><th></th>{quarters.map((q) => <th key={q.end}>{q.fiscal_label ?? q.end}<div className="muted small">{q.end}</div></th>)}</tr>
              </thead>
              <tbody>
                {ROWS.map((key) => {
                  const any = quarters.find((q) => q.metrics[key]);
                  if (!any) return null;
                  return (
                    <tr key={key}>
                      <th>{any.metrics[key].label}</th>
                      {quarters.map((q) => {
                        const m = q.metrics[key];
                        if (!m) return <td key={q.end} className="muted">–</td>;
                        const text = metric(m.value, m.unit) + (m.derived && m.unit === "USD" ? "*" : "");
                        return (
                          <td key={q.end} title={m.derivation ?? t.asReported} className={m.value < 0 ? "neg" : ""}>
                            {m.source ? <a href={m.source} target="_blank" rel="noreferrer">{text}</a> : text}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {segments.length > 0 && (
            <>
              <h3>{t.segmentsTitle} ({data.segments[0].period_end})</h3>
              <div className="scroll segtable">
                <table>
                  <thead><tr><th>{t.segment}</th><th>{t.revenue}</th><th>{t.opIncome}</th><th>{t.margin}</th></tr></thead>
                  <tbody>
                    {segments.map(([member, m]) => (
                      <tr key={member}>
                        <th>{member}</th>
                        {[m.segment_revenue, m.segment_operating_income].map((s, j) => (
                          <td key={j} className={s && s.value < 0 ? "neg" : ""}>
                            {!s ? "–" : s.source ? <a href={s.source} target="_blank" rel="noreferrer">{usd(s.value)}</a> : usd(s.value)}
                          </td>
                        ))}
                        <td title={t.marginTitle}>
                          {segmentMargin(m.segment_revenue?.value, m.segment_operating_income?.value)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <p className="muted small">{data.note}</p>
        </>
      )}
    </section>
  );
}
