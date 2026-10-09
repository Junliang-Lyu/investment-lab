import { useEffect, useState } from "react";
import { api, MacroResponse, MacroSeries } from "../api";
import { navigate } from "../App";
import { useLang } from "../i18n";

// One download per page load is shared by every strip on the page and by later visits to other pages.
let macroOnce: Promise<MacroResponse> | null = null;
function loadMacro(): Promise<MacroResponse> {
  if (!macroOnce) macroOnce = api.macro().catch((e) => { macroOnce = null; throw e; });
  return macroOnce;
}

const SHOWN = ["DGS10", "FEDFUNDS", "CPIAUCSL", "UNRATE"];

function Spark({ s }: { s: MacroSeries }) {
  const from = new Date(s.latest.date + "T00:00:00Z");
  from.setUTCFullYear(from.getUTCFullYear() - 2);
  const pts = s.points.filter((p) => p[0] >= from.toISOString().slice(0, 10));
  if (pts.length < 2) return null;
  const vs = pts.map((p) => p[1]);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${((i / (pts.length - 1)) * 100).toFixed(1)},${(26 - ((p[1] - lo) / span) * 24).toFixed(1)}`).join("");
  return (
    <svg viewBox="0 0 100 28" preserveAspectRatio="none" className="spark" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

/** A compact, read-only strip of macro background. Context, never a signal; the AI does not see it. */
export function MacroStrip({ collapsible = false }: { collapsible?: boolean }) {
  const { t, lang } = useLang();
  const [macro, setMacro] = useState<MacroResponse | null | undefined>(undefined);
  useEffect(() => { loadMacro().then(setMacro).catch(() => setMacro(null)); }, []);
  if (macro === null) return null;  // unavailable: the page works without it
  const series = (macro?.series ?? []).filter((s) => SHOWN.includes(s.id));
  const body = (
    <>
      <p className="muted small">{t.ctxNote}</p>
      {macro === undefined ? <p className="muted small">{t.mmLoading}</p> : (
        <ul className="macrostrip">
          {series.map((s) => (
            <li key={s.id}>
              <span className="small muted">{t.ctxShort[s.id] ?? s.id}</span>
              <b>{s.latest.value.toFixed(2)}%</b>
              <Spark s={s} />
              <span className="small muted">{s.latest.date.slice(0, 7)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="small">
        <a href="/lab/market" onClick={(e) => { e.preventDefault(); navigate("/lab/market"); }}>{t.ctxAll} →</a>
        <span className="muted"> · {t.mkSource}</span>
      </p>
    </>
  );
  return collapsible ? (
    <details className="ctxbox"><summary>{t.ctxTitle}</summary>{body}</details>
  ) : (
    <div className="ctxbox"><b>{t.ctxTitle}</b>{body}</div>
  );
}

/** "Next earnings: about …" for one company; silently absent when the estimate is unavailable. */
export function NextEarnings({ ticker }: { ticker: string }) {
  const { t } = useLang();
  const [next, setNext] = useState<{ estimated: string; past: boolean } | null>(null);
  useEffect(() => {
    setNext(null);
    api.nextEarnings(ticker).then(setNext).catch(() => setNext(null));
  }, [ticker]);
  return next ? <p className="muted small">{t.mmNext(next.estimated, next.past)}</p> : null;
}
