import { useEffect, useMemo, useState } from "react";
import { api, MacroResponse, MacroSeries, CalendarResponse } from "../api";
import { useLang } from "../i18n";

type Span = 1 | 5 | 10;
const W = 640, H = 190, PL = 44, PR = 10, PT = 10, PB = 22;

function cutoff(latest: string, years: number): string {
  const d = new Date(latest + "T00:00:00Z");
  d.setUTCFullYear(d.getUTCFullYear() - years);
  return d.toISOString().slice(0, 10);
}

function ticks(lo: number, hi: number): number[] {
  const span = hi - lo || 1;
  const raw = span / 3;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1000) / 1000);
  return out;
}

function Chart({ s, span, lang }: { s: MacroSeries; span: Span; lang: "zh" | "en" }) {
  const { t } = useLang();
  const [hover, setHover] = useState<number | null>(null);
  const pts = useMemo(() => {
    const from = cutoff(s.latest.date, span);
    return s.points.filter((p) => p[0] >= from);
  }, [s, span]);
  if (pts.length < 2) return null;
  const vals = pts.map((p) => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo || 1) * 0.08;
  lo -= pad; hi += pad;
  const t0 = Date.parse(pts[0][0]), t1 = Date.parse(pts[pts.length - 1][0]);
  const x = (d: string) => PL + ((Date.parse(d) - t0) / (t1 - t0 || 1)) * (W - PL - PR);
  const y = (v: number) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const yt = ticks(lo, hi);
  const shown = hover !== null ? pts[hover] : pts[pts.length - 1];
  const onMove = (clientX: number, el: SVGSVGElement) => {
    const r = el.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    let best = 0, bd = Infinity;
    pts.forEach((p, i) => { const d = Math.abs(x(p[0]) - px); if (d < bd) { bd = d; best = i; } });
    setHover(best);
  };
  const fmt = (v: number) => `${v.toFixed(2)}${s.unit === "pp" ? " pp" : "%"}`;
  return (
    <figure className="mchart">
      <figcaption>
        <b>{s.title[lang]}</b>
        <span className="mval">{fmt(shown[1])} <span className="muted small">{shown[0]}</span></span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${s.title[lang]}: ${fmt(s.latest.value)} (${s.latest.date})`}
           onMouseMove={(e) => onMove(e.clientX, e.currentTarget)} onMouseLeave={() => setHover(null)}
           onTouchMove={(e) => onMove(e.touches[0].clientX, e.currentTarget)} onTouchEnd={() => setHover(null)}>
        {yt.map((v) => (
          <g key={v}>
            <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} className={v === 0 ? "zero" : "grid"} />
            <text x={PL - 6} y={y(v) + 4} textAnchor="end" className="axis">{v}</text>
          </g>
        ))}
        <text x={PL} y={H - 5} className="axis">{pts[0][0].slice(0, 7)}</text>
        <text x={W - PR} y={H - 5} textAnchor="end" className="axis">{pts[pts.length - 1][0].slice(0, 7)}</text>
        <path d={path} className="line" />
        {hover !== null && <circle cx={x(pts[hover][0])} cy={y(pts[hover][1])} r="4" className="dot" />}
      </svg>
      <div className="small muted">
        <a href={s.url} target="_blank" rel="noreferrer">{t.mkSeries(s.id)}</a>
      </div>
    </figure>
  );
}

export default function Market() {
  const { t, lang } = useLang();
  const [macro, setMacro] = useState<MacroResponse | null | undefined>(undefined);
  const [cal, setCal] = useState<CalendarResponse | null | undefined>(undefined);
  const [span, setSpan] = useState<Span>(5);

  useEffect(() => {
    api.macro().then(setMacro).catch(() => setMacro(null));
    api.calendar().then(setCal).catch(() => setCal(null));
  }, []);

  const daysTo = (est: string, today: string) =>
    Math.round((Date.parse(est + "T00:00:00Z") - Date.parse(today + "T00:00:00Z")) / 86400000);

  return (
    <section>
      <h1>{t.mkTitle}</h1>
      <p className="lede">{t.mkLede}</p>

      <h2 className="h2">{t.mkCalTitle}</h2>
      <p className="muted small">{t.mkCalNote}</p>
      {cal === undefined && <p className="muted">{t.mmLoading}</p>}
      {cal === null && <p className="notice">{t.mkCalDown}</p>}
      {cal && (
        <div className="scroll">
          <table className="caltable">
            <thead><tr><th>{t.mkCompany}</th><th>{t.mkLast}</th><th>{t.mkEst}</th><th>{t.mkIn}</th></tr></thead>
            <tbody>
              {cal.items.map((i) => (
                <tr key={i.ticker} className={i.past ? "past" : ""}>
                  <th>{i.ticker}</th>
                  <td>{i.last_reported}</td>
                  <td>{i.past ? "—" : i.estimated}</td>
                  <td>{i.past ? t.mkPast : t.mkDays(daysTo(i.estimated, cal.today))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2 className="h2">{t.mkMacroTitle}</h2>
      <p className="muted small">{t.mkMacroNote}</p>
      <div className="spanbar" role="group" aria-label={t.mkRange}>
        {([1, 5, 10] as Span[]).map((n) => (
          <button key={n} type="button" className={span === n ? "on" : ""} aria-pressed={span === n} onClick={() => setSpan(n)}>
            {t.mkYears(n)}
          </button>
        ))}
      </div>
      {macro === undefined && <p className="muted">{t.mmLoading}</p>}
      {macro === null && <p className="notice">{t.mkMacroDown}</p>}
      {macro && (
        <>
          <div className="mcharts">
            {macro.series.map((s) => <Chart key={s.id} s={s} span={span} lang={lang} />)}
          </div>
          {macro.missing.length > 0 && <p className="muted small">{t.mkMissing(macro.missing.join(", "))}</p>}
          <p className="muted small">{t.mkSource} {macro.updated && t.mkUpdated(macro.updated)}</p>
        </>
      )}
      <p className="muted small">{t.mkDisclaimer}</p>
    </section>
  );
}
