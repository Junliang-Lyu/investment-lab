import { useEffect, useState } from "react";
import { api, ApiError, Decision, LabMemo, MemoAnswers } from "../api";
import { navigate } from "../App";
import { Strings, useLang } from "../i18n";
import { forgetMemo, rememberMemo } from "../memos";
import { Claim } from "./Skeptic";

const ES = ["E1", "E2", "E3"] as const;
const STEP_OF: Record<string, number> = { skeptic_done: 1, user_responded: 2, reviewed: 3, final: 4 };

function pad(xs: string[], n: number): string[] {
  return xs.length >= n ? xs : [...xs, ...Array(n - xs.length).fill("")];
}

function toForm(a: MemoAnswers): MemoAnswers {
  return { ...a, reasons: pad(a.reasons, 3), invalidation: pad(a.invalidation, 3),
           responses: Object.fromEntries(ES.map((e) => [e, a.responses[e] ?? ""])) };
}

function toPayload(f: MemoAnswers): MemoAnswers {
  const keep = (xs: string[]) => xs.map((x) => x.trim()).filter(Boolean);
  return {
    reasons: keep(f.reasons), invalidation: keep(f.invalidation),
    target_weight_pct: f.target_weight_pct && f.target_weight_pct > 0 ? f.target_weight_pct : null,
    responses: Object.fromEntries(ES.map((e) => [e, (f.responses[e] ?? "").trim()]).filter(([, v]) => v)),
    review_date: f.review_date || null, review_focus: f.review_focus.trim(),
  };
}

// A max with a four-digit year makes browsers stop the year field at four digits and move on to the month.
const iso = (d: Date) => d.toISOString().slice(0, 10);
const TODAY = iso(new Date());
const MAX_DATE = iso(new Date(Date.now() + 5 * 366 * 864e5));
const FIELD: Record<string, keyof Strings["mmField"]> = {
  reasons: "a", target_weight_pct: "target", responses: "b", invalidation: "c", review_date: "date", review_focus: "focus",
};

/** Validation errors from the API (a list of {loc, msg}) as a sentence naming the sections to fix. */
function saveError(e: unknown, t: Strings): string {
  if (e instanceof ApiError && e.status === 422 && Array.isArray(e.detail)) {
    const names = [...new Set((e.detail as { loc?: unknown[] }[]).map((d) => {
      const key = (d.loc ?? []).find((x) => typeof x === "string" && x in FIELD) as string | undefined;
      return key ? t.mmField[FIELD[key]] : null;
    }).filter(Boolean))] as string[];
    if (names.length) return t.mmFix(names.join(t.mmSep));
  }
  return (e as Error).message;
}

export default function MemoPage({ id }: { id: string }) {
  const { t } = useLang();
  const [memo, setMemo] = useState<LabMemo | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [form, setForm] = useState<MemoAnswers | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewMsg, setReviewMsg] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision>("watchlist");
  const [reason, setReason] = useState("");
  const [decideMsg, setDecideMsg] = useState<string | null>(null);
  const [showSkeptic, setShowSkeptic] = useState(false);
  const [copied, setCopied] = useState(false);

  function apply(m: LabMemo) {
    setMemo(m);
    setForm(toForm(m.answers));
    setDirty(false);
    rememberMemo({ id: m.id, ticker: m.ticker, thesis: m.thesis, at: m.created_at });
  }

  useEffect(() => {
    api.memo(id).then(apply).catch((e) => {
      if (e instanceof ApiError && e.status === 404) { forgetMemo(id); setNotFound(true); } else setError(e.message);
    });
  }, [id]);

  useEffect(() => {  // warn before leaving with unsaved answers
    if (!dirty) return;
    const on = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", on);
    return () => window.removeEventListener("beforeunload", on);
  }, [dirty]);

  if (notFound) return <section><p className="notice">{t.mmNotFound}</p></section>;
  if (!memo || !form) return <section><p className="muted">{error ?? t.mmLoading}</p></section>;

  const final = memo.status === "final";
  const short = memo.stance === "short";
  const step = STEP_OF[memo.status] ?? 1;
  const sk = memo.skeptic;
  const edit = (patch: Partial<MemoAnswers>) => { setForm({ ...form, ...patch }); setDirty(true); };

  async function save() {
    setSaving(true); setError(null);
    try { apply(await api.saveAnswers(id, toPayload(form!))); }
    catch (e) { setError(saveError(e, t)); }
    finally { setSaving(false); }
  }

  async function review() {
    setReviewing(true); setReviewMsg(null);
    try {
      const r = await api.reviewMemo(id);
      if (r.ok) apply(r.memo);
      else setReviewMsg(t.mmReviewBlocked(r.checks.advice, r.checks.ungrounded_numbers, r.checks.other));
    } catch (e) {
      const s = e instanceof ApiError ? e.status : 0;
      setReviewMsg(t.mmReviewErrors[s] ?? (e as Error).message);
    } finally { setReviewing(false); }
  }

  async function decide() {
    setDecideMsg(null);
    try { apply(await api.finalizeMemo(id, decision, reason.trim() || null)); }
    catch (e) { setDecideMsg((e as Error).message); }
  }

  async function reopen() {
    try { apply(await api.reopenMemo(id)); } catch (e) { setDecideMsg((e as Error).message); }
  }

  async function remove() {
    if (!window.confirm(t.mmDeleteConfirm)) return;
    try { await api.deleteMemo(id); forgetMemo(id); navigate("/lab"); } catch (e) { setError((e as Error).message); }
  }

  function copy() {
    navigator.clipboard?.writeText(window.location.origin + `/lab/memo/${id}`).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000);
    }).catch(() => {});
  }

  const needReason = memo.status === "user_responded" || (decision === "eligible_for_gate" && memo.review_unresolved);
  const lastFinal = [...memo.events].reverse().find((e) => e.to === "final");

  return (
    <section className="memo">
      <h1>{t.mmTitle(memo.ticker)} <span className="muted small">{t.stance[short ? "short" : "long"]} · {t.mmVersion(memo.version)}</span></h1>
      <ol className="stepper">
        {t.mmSteps.map((s, i) => (
          <li key={s} className={i < step ? "done" : i === step ? "now" : ""}><span className="num">{i + 1}</span>{s}</li>
        ))}
      </ol>
      <div className="linkbar">
        <p className="muted small">{t.mmLinkNote(memo.expires_at)}</p>
        <div className="actions">
          <button type="button" className="secondary" onClick={copy}>{copied ? t.mmCopied : t.mmCopy}</button>
          <a className="secondary" href={api.memoMarkdownUrl(id)} download>{t.mmDownload}</a>
          <button type="button" className="linkish danger" onClick={remove}>{t.mmDelete}</button>
        </div>
      </div>

      <h2 className="h2">1 · {t.mmSteps[0]}</h2>
      <p className="restated"><span className="muted small">{t.mmThesis}</span><br />{memo.thesis}</p>
      <h3>{t.mmSkepticTitle}</h3>
      <ol className="claims">{sk.bear_case.map((c) => <Claim key={c.claim} c={c} r={sk} t={t} bear />)}</ol>
      <p><b>{t.skWeakest}:</b> {sk.weakest_assumption}</p>
      <button type="button" className="linkish" onClick={() => setShowSkeptic(!showSkeptic)}>
        {showSkeptic ? t.mmSkepticHide : t.mmSkepticShow}
      </button>
      {showSkeptic && (
        <div className="answer">
          <h3>{t.skBull}</h3>
          <ul className="claims">{sk.bull_case.map((c) => <Claim key={c.claim} c={c} r={sk} t={t} />)}</ul>
          <h3>{t.skVerify}</h3>
          <ul>{sk.verify_questions.map((q) => <li key={q.question}>{q.question} <span className="muted small">({t.skWhere}: {q.where_to_check})</span></li>)}</ul>
        </div>
      )}

      <h2 className="h2">2 · {t.mmSteps[1]}</h2>
      <fieldset className="answers" disabled={final || saving}>
        <h3>{short ? t.mmAShort : t.mmA}</h3>
        <p className="muted small">{short ? t.mmAHelpShort : t.mmAHelp}</p>
        {form.reasons.map((r, i) => (
          <textarea key={i} rows={2} maxLength={500} value={r} placeholder={t.mmReasonPh(i + 1)}
                    onChange={(e) => edit({ reasons: form.reasons.map((x, j) => (j === i ? e.target.value : x)) })} />
        ))}
        {form.reasons.length < 5 && !final && (
          <button type="button" className="linkish" onClick={() => edit({ reasons: [...form.reasons, ""] })}>+ {t.mmAddReason}</button>
        )}
        <label className="inline">{short ? t.mmTargetShort : t.mmTarget}
          <input type="number" min={0} max={100} step={0.1} value={form.target_weight_pct ?? ""}
                 onChange={(e) => edit({ target_weight_pct: e.target.value === "" ? null : Number(e.target.value) })} />
        </label>

        <h3>{t.mmB}</h3>
        <p className="muted small">{t.mmBHelp}</p>
        {ES.map((e, i) => (
          <div key={e} className="respond">
            <p><b>{e}.</b> {sk.bear_case[i]?.plain_summary ?? sk.bear_case[i]?.claim}</p>
            <textarea rows={3} maxLength={1000} value={form.responses[e] ?? ""} placeholder={t.mmBPh}
                      onChange={(ev) => edit({ responses: { ...form.responses, [e]: ev.target.value } })} />
          </div>
        ))}

        <h3>{t.mmC}</h3>
        <p className="muted small">{t.mmCHelp}</p>
        {form.invalidation.map((c, i) => (
          <input key={i} className="wide" maxLength={300} value={c} placeholder={t.mmCPh(i + 1)}
                 onChange={(e) => edit({ invalidation: form.invalidation.map((x, j) => (j === i ? e.target.value : x)) })} />
        ))}
        {form.invalidation.length < 6 && !final && (
          <button type="button" className="linkish" onClick={() => edit({ invalidation: [...form.invalidation, ""] })}>+ {t.mmAddCondition}</button>
        )}
        {sk.invalidation_suggestions.length > 0 && (
          <div className="hint">
            <p className="muted small">{t.mmCSuggest}</p>
            <ul className="small muted">
              {sk.invalidation_suggestions.map((s) => (
                <li key={s.condition}>{s.observable_metric}{s.threshold ? ` (${s.threshold})` : ""}</li>
              ))}
            </ul>
          </div>
        )}

        <h3>{t.mmD}</h3>
        <div className="row-fields">
          <label className="inline">{t.mmDate}
            <input type="date" min={TODAY} max={MAX_DATE} value={form.review_date ?? ""}
                   onChange={(e) => edit({ review_date: e.target.value || null })} />
          </label>
          <label className="inline grow">{t.mmFocus}
            <input className="wide" maxLength={300} value={form.review_focus} onChange={(e) => edit({ review_focus: e.target.value })} />
          </label>
        </div>
      </fieldset>
      {!final && (
        <div className={`savebar${dirty ? " sticky" : ""}`}>
          <button type="button" className="primary" onClick={save} disabled={saving || !dirty}>{saving ? t.mmSaving : t.mmSave}</button>
          <span className={`small ${dirty ? "warn" : "muted"}`}>{dirty ? t.mmUnsaved : t.mmSaved}</span>
          {dirty && memo.review && <span className="muted small">{t.mmSaveClearsReview}</span>}
          {error && <span className="error small">{error}</span>}
        </div>
      )}
      {!final && memo.missing.length > 0 && (
        <div className="notice small">
          {t.mmMissing}
          <ul>{memo.missing.map((m) => <li key={m}>{t.mmMissingMap[m] ?? m}</li>)}</ul>
        </div>
      )}

      <h2 className="h2">3 · {t.mmReviewTitle}</h2>
      <p className="muted small">{t.mmReviewLede}</p>
      {memo.review ? (
        <div className="review">
          <p className="muted small">{memo.review_meta && t.mmReviewedAt(memo.review_meta.at.slice(0, 10), memo.review_meta.model ?? "")}</p>
          <p><b>§A</b> <span className={`verdict ${memo.review.section_a.verdict}`}>{t.mmVerdict[memo.review.section_a.verdict]}</span></p>
          {memo.review.section_a.issues.length > 0 && <ul className="small">{memo.review.section_a.issues.map((x) => <li key={x}>{x}</li>)}</ul>}
          {memo.review.responses.map((r) => (
            <p key={r.counter}><b>§B {r.counter}</b> <span className={`verdict ${r.verdict}`}>{t.mmVerdict[r.verdict]}</span><br /><span className="small">{r.comment}</span></p>
          ))}
          <p><b>{t.mmFactInf}:</b> <span className="small">{memo.review.fact_vs_inference}</span></p>
          <p><b>§C</b> <span className={`verdict ${memo.review.section_c.verdict}`}>{t.mmVerdict[memo.review.section_c.verdict]}</span></p>
          {memo.review.section_c.issues.length > 0 && <ul className="small">{memo.review.section_c.issues.map((x) => <li key={x}>{x}</li>)}</ul>}
          <p><b>§D</b> <span className={`verdict ${memo.review.section_d.verdict}`}>{t.mmVerdict[memo.review.section_d.verdict]}</span>
            {memo.review.section_d.comment && <><br /><span className="small">{memo.review.section_d.comment}</span></>}</p>
          <p><b>{t.mmSummary}:</b> {memo.review.summary}</p>
          {memo.review_unresolved && <p className="notice small">{t.mmReviewUnresolved}</p>}
        </div>
      ) : (
        <div className="actions">
          <button type="button" className="primary" onClick={review}
                  disabled={reviewing || dirty || memo.status !== "user_responded"}>{t.mmReviewBtn}</button>
          {memo.status === "skeptic_done" && <span className="muted small">{t.mmReviewNeedAnswers}</span>}
        </div>
      )}
      {reviewing && <p className="muted working">{t.mmReviewWorking}</p>}
      {reviewMsg && <p className="notice small">{reviewMsg}</p>}

      <h2 className="h2">4 · {t.mmDecisionTitle}</h2>
      <p className="muted small">{t.mmDecisionLede}</p>
      {final ? (
        <div className="decided">
          <p className="overall ok">{t.mmFinal(t.mmDecisions[memo.decision ?? ""] ?? "", lastFinal?.at.slice(0, 10) ?? "")}</p>
          {lastFinal?.reason && <p className="small"><b>{t.mmReason}:</b> {lastFinal.reason}</p>}
          <button type="button" className="linkish" onClick={reopen}>{t.mmReopen}</button>
        </div>
      ) : (
        <fieldset className="decide" disabled={memo.status === "skeptic_done" || dirty}>
          {(["watchlist", "paper", "eligible_for_gate"] as Decision[]).map((d) => (
            <label key={d} className="radio">
              <input type="radio" name="decision" checked={decision === d} onChange={() => setDecision(d)} /> {t.mmDecisions[d]}
            </label>
          ))}
          {needReason && (
            <label className="inline grow">
              {memo.status === "user_responded" ? t.mmReasonSkip : t.mmReasonUnresolved}
              <textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
          )}
          <div className="actions">
            <button type="button" className="primary" onClick={decide} disabled={needReason && !reason.trim()}>{t.mmFinalize}</button>
          </div>
        </fieldset>
      )}
      {decideMsg && <p className="error small">{decideMsg}</p>}

      <h2 className="h2">5 · {t.mmGateTitle}</h2>
      <p className="muted small">{t.mmGateLede}</p>
      <div className="actions">
        <button type="button" className={final ? "primary" : "secondary"}
                onClick={() => navigate("/lab/gate", { memo: id, symbol: memo.ticker, side: short ? "sell" : "buy" })}>{t.mmGateBtn} →</button>
        {!final && <span className="muted small">{t.mmNeedsDecision}</span>}
      </div>
    </section>
  );
}
