import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, EvalReport, SkepticClaim, SkepticResponse, SkepticResult, SkepticStatus } from "../api";
import { navigate } from "../App";
import { pct } from "../format";
import { Strings, useLang } from "../i18n";
import { rememberMemo } from "../memos";

export function Claim({ c, r, t, bear }: { c: SkepticClaim; r: SkepticResult; t: Strings; bear?: boolean }) {
  const plain = !!c.plain_summary;
  const detail = (
    <>
      {!plain ? null : <div className="claimtext"><span className={`ctype ${c.type}`}>{t.skType[c.type] ?? c.type}</span> {c.claim}</div>}
      {c.evidence_refs.length > 0 && (
        <div className="refs">
          {c.evidence_refs.map((ref) => {
            const e = r.evidence[ref];
            if (!e) return null;
            const text = `${e.label} · ${e.period} = ${e.display}${e.derived ? "*" : ""}`;
            return (
              <span key={ref} className="ref" title={e.note ?? undefined}>
                {e.source ? <a href={e.source} target="_blank" rel="noreferrer">{text}</a> : text}
              </span>
            );
          })}
        </div>
      )}
      {c.quotes?.map((q) => {
        const s = r.sources?.[q.source_id];
        return (
          <blockquote key={q.text}>
            “{q.text}”
            {s && <cite> — {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.document}</a> : s.document}</cite>}
          </blockquote>
        );
      })}
      {c.why_it_matters && <div className="sub"><b>{t.skWhy}:</b> {c.why_it_matters}</div>}
    </>
  );
  return (
    <li className="claim">
      {plain ? (
        <>
          <div className="plain">
            {c.category && <span className={`ccat ${c.category}`}>{t.skCat[c.category] ?? c.category}</span>} {c.plain_summary}{" "}
            <span className={`ctype ${c.type}`}>{t.skType[c.type] ?? c.type}</span>
          </div>
          {bear && c.breaks_assumption && <div className="sub"><b>{t.skBreaks}:</b> {c.breaks_assumption}</div>}
          <details className="more">
            <summary>{t.skDetails}</summary>
            {detail}
          </details>
        </>
      ) : (
        <>
          <span className={`ctype ${c.type}`}>{t.skType[c.type] ?? c.type}</span> {c.claim}
          {detail}
          {bear && c.breaks_assumption && <div className="sub"><b>{t.skBreaks}:</b> {c.breaks_assumption}</div>}
        </>
      )}
    </li>
  );
}

function Evals({ report, t }: { report: EvalReport; t: Strings }) {
  const s = report.summary;
  const rows: [string, string, boolean][] = [
    [t.evShownAdvice, String(s.shown_advice), s.shown_advice === 0],
    [t.evLeaks, String(s.injection_leaks), s.injection_leaks === 0],
    [t.evFake, String(s.fact_restated_fake_numbers), s.fact_restated_fake_numbers === 0],
    [t.evOk, pct(s.final_ok_rate), s.final_ok_rate >= 0.95],
    [t.evSchema, pct(s.schema_valid_rate), s.schema_valid_rate >= 0.98],
  ];
  return (
    <section className="evals">
      <h3>{t.evTitle}</h3>
      <p className="muted small">{t.evLede(s.cases, report.run_at.slice(0, 10))}</p>
      <table className="evtable">
        <tbody>
          {rows.map(([k, v, good]) => (
            <tr key={k}><th>{k}</th><td className={good ? "good" : "neg"}>{v}</td></tr>
          ))}
        </tbody>
      </table>
      <p className={`small ${s.passed ? "good" : "neg"}`}>{s.passed ? t.evPassed : t.evFailed} · {report.model} · {report.prompt_version}</p>
    </section>
  );
}

export default function Skeptic() {
  const { t, lang } = useLang();
  const [tickers, setTickers] = useState<string[]>(["GOOG"]);
  const [ticker, setTicker] = useState(() => new URLSearchParams(window.location.search).get("ticker")?.toUpperCase() || "GOOG");
  const [thesis, setThesis] = useState("");
  const [status, setStatus] = useState<SkepticStatus | null>(null);
  const [examples, setExamples] = useState<{ id: string; text: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<SkepticResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [asked, setAsked] = useState<{ ticker: string; thesis: string; stance: string } | null>(null);
  const [stance, setStance] = useState<"long" | "short">(
    new URLSearchParams(window.location.search).get("stance") === "short" ? "short" : "long");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api.companies().then((c) => setTickers(c.map((x) => x.ticker))).catch(() => {});
    api.skepticStatus().then(setStatus).catch(() => setStatus({ enabled: false, reason: "" }));
    api.evals().then(setReport).catch(() => setReport(null));
  }, []);
  useEffect(() => { setExamples(null); }, [ticker]);

  async function showExamples() {
    if (examples) { setExamples(null); return; }
    try { setExamples(await api.theses(ticker, lang)); } catch { setExamples([]); }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setAnswer(null);
    try {
      setAnswer(await api.skeptic({ ticker, thesis, lang, stance }));
      setAsked({ ticker, thesis, stance });
      api.skepticStatus().then(setStatus).catch(() => {});
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(t.skErrors[status] ?? (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function startMemo() {
    if (!asked) return;
    setCreating(true); setError(null);
    try {
      const m = await api.createMemo({ ticker: asked.ticker, thesis: asked.thesis, lang, stance: asked.stance });
      rememberMemo({ id: m.id, ticker: m.ticker, thesis: m.thesis, at: m.created_at });
      navigate(`/lab/memo/${m.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setCreating(false);
    }
  }

  const enabled = status?.enabled === true;
  const r = answer?.ok ? answer.result : null;
  return (
    <section>
      <h1>{t.skTitle}</h1>
      <p className="lede">{t.skLede}</p>

      {status && !enabled && <p className="notice">{t.skDisabled}</p>}

      <form className="skeptic" onSubmit={submit}>
        <label>{t.company}{" "}
          <select value={ticker} onChange={(e) => setTicker(e.target.value)} disabled={busy}>
            {tickers.map((x) => <option key={x}>{x}</option>)}
          </select>
        </label>
        <div className="stance" role="radiogroup" aria-label={t.stanceLabel}>
          <span className="muted small">{t.stanceLabel}</span>
          {(["long", "short"] as const).map((s) => (
            <label key={s}><input type="radio" name="stance" checked={stance === s} disabled={busy}
                                  onChange={() => setStance(s)} /> {t.stance[s]}</label>
          ))}
          {stance === "short" && <span className="muted small">{t.stanceHelp}</span>}
        </div>
        <label className="thesis">{t.skThesis}
          <textarea value={thesis} maxLength={400} rows={3} placeholder={t.skPlaceholder} disabled={busy}
                    onChange={(e) => setThesis(e.target.value)} />
          <span className="muted small count">{thesis.length}/400</span>
        </label>
        <div className="actions">
          <button type="submit" disabled={!enabled || busy || thesis.trim().length < 10}>{t.skSubmit}</button>
          <button type="button" className="linkish" onClick={showExamples}>
            {examples ? t.skExamplesHide : t.skExamplesShow}
          </button>
          {enabled && status && (
            <span className="muted small">{t.skRemaining(status.visitor_remaining, status.per_visitor_daily)}</span>
          )}
        </div>
        {examples && (
          <div className="examples">
            <p className="muted small">{t.skExamplesNote}</p>
            {examples.map((x) => (
              <button type="button" key={x.id} className="example" onClick={() => setThesis(x.text)}>{x.text}</button>
            ))}
          </div>
        )}
      </form>

      {busy && <p className="muted working">{t.skWorking}</p>}
      {error && <p className="error">{error}</p>}
      {answer && !answer.ok && (
        <p className="notice">{t.skBlocked(answer.checks.advice, answer.checks.ungrounded_numbers, answer.checks.other)}</p>
      )}
      {answer?.ok && r && (
        <div className="answer">
          {answer.cached && <p className="muted small">{t.skCached}</p>}
          <p className="restated"><span className="muted small">{t.skRestated}{asked ? ` · ${t.stance[asked.stance]}` : ""}</span><br />{r.thesis_restated}</p>
          <h3>{t.skBear}</h3>
          <ol className="claims">{r.bear_case.map((c) => <Claim key={c.claim} c={c} r={r} t={t} bear />)}</ol>
          <h3>{t.skBull}</h3>
          <ul className="claims">{r.bull_case.map((c) => <Claim key={c.claim} c={c} r={r} t={t} />)}</ul>
          <h3>{t.skWeakest}</h3>
          <p>{r.weakest_assumption}</p>
          <h3>{t.skInvalidation}</h3>
          <ul>
            {r.invalidation_suggestions.map((i) => (
              <li key={i.condition}>{i.condition} — <span className="muted">{i.observable_metric}{i.threshold ? ` (${i.threshold})` : ""}</span></li>
            ))}
          </ul>
          <h3>{t.skVerify}</h3>
          <ul>
            {r.verify_questions.map((q) => (
              <li key={q.question}>{q.question} <span className="muted small">({t.skWhere}: {q.where_to_check})</span></li>
            ))}
          </ul>
          {r.computed && r.computed.length > 0 && (
            <details className="computed">
              <summary className="muted small">{t.skComputed(r.computed.length)}</summary>
              <ul className="small">{r.computed.map((f) => <li key={f}><code>{f.replace("/ … - 1", "/ … − 1")}</code></li>)}</ul>
            </details>
          )}
          <p className="muted small">{t.skMeta(answer.model ?? "?", answer.prompt_version)}</p>
          <div className="next">
            <button type="button" className="primary" onClick={startMemo} disabled={creating || !asked}>{t.skContinue} →</button>
            <p className="muted small">{t.skContinueNote}</p>
          </div>
        </div>
      )}

      {report && <Evals report={report} t={t} />}
    </section>
  );
}
