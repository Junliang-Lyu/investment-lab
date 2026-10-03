import { useEffect, useState } from "react";
import { api, ApiError, ExplainResponse } from "../api";
import { useLang } from "../i18n";
import { Claim } from "./Skeptic";

type State = { kind: "loading" } | { kind: "ok"; data: Extract<ExplainResponse, { ok: true }> } | { kind: "none" } | { kind: "error"; status: number };

// Plain-language reading of a company's latest quarter. Loads by itself when the company page opens; the server
// caches it per company, language and quarter, so most visits are instant.
export default function Explainer({ ticker }: { ticker: string }) {
  const { t, lang } = useLang();
  const [state, setState] = useState<State>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    api.explain(ticker, lang)
      .then((d) => { if (live) setState(d.ok ? { kind: "ok", data: d } : { kind: "none" }); })
      .catch((e) => { if (live) setState({ kind: "error", status: e instanceof ApiError ? e.status : 0 }); });
    return () => { live = false; };
  }, [ticker, lang]);

  const ok = state.kind === "ok" ? state.data : null;
  const r = ok?.result;
  return (
    <section className="explain">
      <h2>{t.explTitle}</h2>
      <p className="muted small">{t.explLede}</p>
      {state.kind === "loading" && <p className="working status" role="status"><span className="spinner" aria-hidden="true" /> {t.explWorking}</p>}
      {state.kind === "none" && <p className="notice">{t.explNone}</p>}
      {state.kind === "error" && <p className="muted">{t.explErrors[state.status] ?? t.explErrors[0]}</p>}
      {ok && r && (
        <>
          {!ok.evaluated && <p className="notice small">{t.notEvaluated}</p>}
          <h3>{t.explWell}</h3>
          <ul className="claims">{r.bull_case.map((c) => <Claim key={c.claim} c={c} r={r} t={t} />)}</ul>
          <h3>{t.explWatch}</h3>
          <ol className="claims">{r.bear_case.map((c) => <Claim key={c.claim} c={c} r={r} t={t} bear breaksLabel={t.explBreaks} />)}</ol>
          <p><b>{t.explOpen}:</b> {r.weakest_assumption}</p>
          <h3>{t.explNext}</h3>
          <ul className="claims">
            {r.invalidation_suggestions.map((i) => (
              <li key={i.condition}>{i.condition} <span className="muted small">({i.observable_metric}{i.threshold ? ` · ${i.threshold}` : ""})</span></li>
            ))}
          </ul>
          <details className="more">
            <summary>{t.skVerify}</summary>
            <ul className="small">
              {r.verify_questions.map((v) => <li key={v.question}>{v.question} <span className="muted">· {t.skWhere}: {v.where_to_check}</span></li>)}
            </ul>
          </details>
          {r.computed && r.computed.length > 0 && (
            <details className="computed">
              <summary className="muted small">{t.skComputed(r.computed.length)}</summary>
              <ul className="small">{r.computed.map((f) => <li key={f}><code>{f.replace("/ … - 1", "/ … − 1")}</code></li>)}</ul>
            </details>
          )}
          <p className="muted small">{t.explMeta(ok.period, ok.model ?? "?", ok.prompt_version)}</p>
        </>
      )}
    </section>
  );
}
