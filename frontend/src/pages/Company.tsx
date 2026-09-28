import { useEffect, useMemo, useRef, useState } from "react";
import { api, Snapshot } from "../api";
import { metric, pct, usd } from "../format";

const ROWS = ["revenue", "gross_margin", "operating_income", "operating_margin", "net_income", "cfo", "capex", "fcf",
  "cash_and_investments", "net_cash"];

// Margins beyond ±100% (tiny revenue, large losses) are shown as not meaningful.
function segmentMargin(rev?: number, oi?: number): string {
  if (rev === undefined || oi === undefined || rev <= 0) return "–";
  const m = oi / rev;
  return Math.abs(m) > 1 ? "n/m" : pct(m);
}

export default function Company() {
  const [tickers, setTickers] = useState<string[]>([]);
  const [ticker, setTicker] = useState("GOOG");
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { api.companies().then((c) => setTickers(c.map((x) => x.ticker))).catch(() => setTickers(["GOOG"])); }, []);
  useEffect(() => {
    setLoading(true); setError(null);
    api.snapshot(ticker).then(setData).catch((e) => { setData(null); setError(String(e.message ?? e)); })
      .finally(() => setLoading(false));
  }, [ticker]);

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
      <h1>Financial snapshot</h1>
      <p className="lede">Company totals from SEC filings. Values marked * are computed from reported figures (for example Q4 = full year − nine months); hover or long-press a value to see how. Scroll sideways for older quarters.</p>
      <div className="toolbar">
        <label>Company{" "}
          <select value={ticker} onChange={(e) => setTicker(e.target.value)}>
            {tickers.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        {data && <span className="muted">{data.company} · CIK {data.cik}</span>}
      </div>
      {loading && <p className="muted">Loading filings…</p>}
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
                          <td key={q.end} title={m.derivation ?? "As reported"} className={m.value < 0 ? "neg" : ""}>
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
              <h3>Segments, latest quarter ({data.segments[0].period_end})</h3>
              <div className="scroll segtable">
                <table>
                  <thead><tr><th>Segment</th><th>Revenue</th><th>Op. income</th><th>Margin*</th></tr></thead>
                  <tbody>
                    {segments.map(([member, m]) => (
                      <tr key={member}>
                        <th>{member}</th>
                        {[m.segment_revenue, m.segment_operating_income].map((s, j) => (
                          <td key={j} className={s && s.value < 0 ? "neg" : ""}>
                            {!s ? "–" : s.source ? <a href={s.source} target="_blank" rel="noreferrer">{usd(s.value)}</a> : usd(s.value)}
                          </td>
                        ))}
                        <td title="segment operating income / segment revenue">
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
