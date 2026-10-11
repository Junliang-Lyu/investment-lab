import { useEffect, useState } from "react";
import { api, Insiders } from "../api";
import { usd } from "../format";
import { useLang } from "../i18n";

const SHOWN = 12;

/** Open-market insider buys and sells from Form 4 filings. Context only; the note says it is not a signal. */
export default function InsiderTrades({ ticker }: { ticker: string }) {
  const { t } = useLang();
  const [data, setData] = useState<Insiders | null>(null);
  const [error, setError] = useState(false);
  const [all, setAll] = useState(false);
  useEffect(() => {
    setData(null); setError(false); setAll(false);
    api.insiders(ticker).then(setData).catch(() => setError(true));
  }, [ticker]);

  if (error) return <div className="insiders"><h3>{t.inTitle}</h3><p className="muted small">{t.inErr}</p></div>;
  if (!data) return <div className="insiders"><h3>{t.inTitle}</h3><p className="muted small">{t.inLoading}</p></div>;
  const rows = all ? data.trades : data.trades.slice(0, SHOWN);
  const side = (label: string, s: Insiders["buys"], cls: string) => (
    <div className={`sum ${cls}`}>
      <span className="muted small">{label}</span>
      <b>{s.trades === 0 ? "–" : usd(s.value)}</b>
      {s.trades > 0 && <span className="small">{t.inTradesBy(s.trades, s.people)}</span>}
      {s.planned > 0 && <div className="muted small">{t.inPlanned(s.planned)}</div>}
    </div>
  );
  return (
    <div className="insiders">
      <h3>{t.inTitle}</h3>
      <p className="muted small">{t.inNote}</p>
      <div className="sums">{side(t.inBuys, data.buys, "buy")}{side(t.inSells, data.sells, "sell")}</div>
      {data.trades.length === 0 ? <p className="muted">{t.inNone}</p> : (
        <div className="scroll">
          <table>
            <thead>
              <tr><th>{t.inDate}</th><th>{t.inWho}</th><th>{t.inRole}</th><th>{t.inSide}</th>
                <th className="num">{t.inShares}</th><th className="num">{t.inPrice}</th><th className="num">{t.inValue}</th></tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.url}-${i}`}>
                  <td><a href={r.url} target="_blank" rel="noreferrer" title={t.inSource}>{r.date}</a></td>
                  <td>{r.owner}{!r.direct && <span className="tag" title={t.inIndirectTitle}>{t.inIndirect}</span>}</td>
                  <td className="small">{r.roles.join(", ") || "–"}</td>
                  <td>{r.side === "buy" ? t.inBuy : t.inSell}
                    {r.plan_10b5_1 && <span className="tag" title={t.inPlanTitle}>{t.inPlan}</span>}</td>
                  <td className="num">{r.shares.toLocaleString("en-US", { maximumFractionDigits: 0 })}</td>
                  <td className="num">{r.price == null ? t.inPriceNone : `$${r.price.toFixed(2)}`}</td>
                  <td className="num">{r.value == null ? "–" : usd(r.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.trades.length > SHOWN && (
        <button type="button" className="secondary" onClick={() => setAll(!all)}>
          {all ? t.inLess : t.inAll(data.trades.length)}
        </button>
      )}
      <p className="muted small">
        {data.other_transactions > 0 && t.inOther(data.other_transactions)}{" "}
        {data.truncated && t.inTrunc(data.read + data.failed, data.filings)}{" "}
        {data.failed > 0 && t.inFailed(data.failed)}
      </p>
    </div>
  );
}
