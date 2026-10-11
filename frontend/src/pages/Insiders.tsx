import { Fragment, useEffect, useMemo, useState } from "react";
import { api, Insiders, InsiderTrade } from "../api";
import { usd } from "../format";
import { useLang } from "../i18n";

const int = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });

type Group = {
  key: string; owner: string; coOwners: number; roles: string[]; side: "buy" | "sell"; trades: InsiderTrade[];
  shares: number; value: number; priced: number; planned: number; first: string; last: string; pct: number | null;
};

/** One row per person and side. `pct` is the size of the trades against what the person held before the first one,
 *  and only when that can be worked out from one class of shares held directly. */
function group(trades: InsiderTrade[]): Group[] {
  const by = new Map<string, InsiderTrade[]>();
  trades.forEach((r) => by.set(`${r.owner}|${r.side}`, [...(by.get(`${r.owner}|${r.side}`) ?? []), r]));
  const out: Group[] = [];
  by.forEach((rs, key) => {
    const asc = [...rs].sort((a, b) => a.date.localeCompare(b.date));
    const first = asc[0];
    const one = rs.every((r) => r.direct && r.security === first.security && r.after != null);
    const shares = rs.reduce((a, r) => a + r.shares, 0);
    const before = one ? (first.side === "sell" ? first.after! + first.shares : first.after! - first.shares) : 0;
    const pct = one && before > 0 && shares / before <= 1 ? Math.round((shares / before) * 100) : null;
    out.push({
      key, owner: first.owner, coOwners: first.co_owners, roles: first.roles, side: first.side, trades: rs, shares,
      value: rs.reduce((a, r) => a + (r.value ?? 0), 0), priced: rs.filter((r) => r.value != null).length,
      planned: rs.filter((r) => r.plan_10b5_1).length, first: first.date, last: asc[asc.length - 1].date, pct,
    });
  });
  return out.sort((a, b) => b.value - a.value || b.shares - a.shares);
}

function Months({ data }: { data: Insiders }) {
  const { t } = useLang();
  const W = 640, H = 120, PAD = 22, n = data.months.length;
  const max = Math.max(1, ...data.months.flatMap((m) => [m.buy, m.sell]));
  const slot = (W - 8) / n, bw = Math.min(14, slot / 2 - 2);
  const h = (v: number) => (v / max) * (H - PAD - 6);
  return (
    <svg className="months" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t.inMonthly}>
      <line x1="0" x2={W} y1={H - PAD} y2={H - PAD} stroke="var(--line)" />
      <text x="2" y="10" fontSize="10" fill="var(--muted)">{usd(max)}</text>
      {data.months.map((m, i) => {
        const cx = 4 + slot * i + slot / 2;
        return (
          <g key={m.month}>
            <title>{t.inMonthTip(m.month, usd(m.buy), usd(m.sell))}</title>
            <rect x={cx - bw - 1} y={H - PAD - h(m.buy)} width={bw} height={h(m.buy)} fill="var(--accent)" rx="2" />
            <rect x={cx + 1} y={H - PAD - h(m.sell)} width={bw} height={h(m.sell)} fill="var(--info)" rx="2" />
            <text x={cx} y={H - 6} fontSize="10" textAnchor="middle" fill="var(--muted)">{m.month.slice(5)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Open-market insider buys and sells from Form 4 filings. Context only; the note says it is not a signal. */
export default function InsiderTrades({ ticker }: { ticker: string }) {
  const { t } = useLang();
  const [data, setData] = useState<Insiders | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    setData(null); setError(false); setOpen(null);
    api.insiders(ticker).then(setData).catch(() => setError(true));
  }, [ticker]);
  const groups = useMemo(() => group(data?.trades ?? []), [data]);

  if (error) return <div className="insiders"><h3>{t.inTitle}</h3><p className="muted small">{t.inErr}</p></div>;
  if (!data) return <div className="insiders"><h3>{t.inTitle}</h3><p className="muted small">{t.inLoading}</p></div>;
  const side = (label: string, s: Insiders["buys"]) => (
    <div className="sum">
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
      <div className="sums">{side(t.inBuys, data.buys)}{side(t.inSells, data.sells)}</div>
      {data.trades.length === 0 ? <p className="muted">{t.inNone}</p> : (
        <>
          <b className="small">{t.inMonthly}</b>
          <div className="legend">
            <span><i style={{ background: "var(--accent)" }} />{t.inBuy}</span>
            <span><i style={{ background: "var(--info)" }} />{t.inSell}</span>
          </div>
          <Months data={data} />
          <b className="small">{t.inPerson}</b>
          <div className="scroll">
            <table className="instbl">
              <thead>
                <tr><th>{t.inWho}</th><th>{t.inSide}</th><th className="r">{t.inCount}</th><th className="r">{t.inShares}</th>
                  <th className="r">{t.inAvg}</th><th className="r">{t.inValue}</th><th>{t.inPeriod}</th></tr>
              </thead>
              <tbody>
                {groups.map((g) => (
                  <Fragment key={g.key}>
                    <tr className="grp" onClick={() => setOpen(open === g.key ? null : g.key)}>
                      <td>
                        <span className="caret">{open === g.key ? "▾" : "▸"}</span>{g.owner}
                        {g.coOwners > 0 && <span className="tag">+{g.coOwners}</span>}
                        <span className="sub">{g.roles.join(", ") || "–"}</span>
                      </td>
                      <td>{g.side === "buy" ? t.inBuy : t.inSell}
                        {g.planned > 0 && <span className="sub">{g.planned === g.trades.length ? t.inPlanAll : t.inPlanSome(g.planned, g.trades.length)}</span>}
                      </td>
                      <td className="r">{g.trades.length}</td>
                      <td className="r">{int(g.shares)}{g.pct != null && <span className="sub">{t.inPctHeld(g.pct)}</span>}</td>
                      <td className="r">{g.priced === g.trades.length && g.shares > 0 ? `$${(g.value / g.shares).toFixed(2)}` : "–"}</td>
                      <td className="r">{g.priced > 0 ? usd(g.value) : "–"}</td>
                      <td>{g.first === g.last ? g.first : `${g.first} → ${g.last}`}</td>
                    </tr>
                    {open === g.key && g.trades.map((r, i) => (
                      <tr className="det" key={`${r.url}-${i}`}>
                        <td colSpan={2}>
                          <a href={r.url} target="_blank" rel="noreferrer" title={t.inSource}>{r.date}</a>
                          {r.plan_10b5_1 && <span className="tag" title={t.inPlanTitle}>{t.inPlan}</span>}
                          {!r.direct && <span className="tag" title={t.inIndirectTitle}>{t.inIndirect}</span>}
                          {r.security && <span className="sub">{r.security}</span>}
                        </td>
                        <td className="r"></td>
                        <td className="r">{int(r.shares)}{r.after != null && <span className="sub">{t.inAfter} {int(r.after)}</span>}</td>
                        <td className="r">{r.price == null ? t.inPriceNone : `$${r.price.toFixed(2)}`}</td>
                        <td className="r">{r.value == null ? "–" : usd(r.value)}</td>
                        <td></td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p className="muted small">
        {data.other_transactions > 0 && t.inOther(data.other_transactions)}{" "}
        {data.as_owner > 0 && t.inAsOwner(data.as_owner)}{" "}
        {data.truncated && t.inTrunc(data.read + data.failed, data.filings)}{" "}
        {data.failed > 0 && t.inFailed(data.failed)}
      </p>
    </div>
  );
}
