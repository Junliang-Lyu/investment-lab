import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, ReferenceItem, ReferenceProfile } from "../api";
import { navigate } from "../App";
import { pct, usd } from "../format";
import { useLang } from "../i18n";

const KINDS = ["new", "exited", "increased", "decreased"] as const;

const CJK = /[\u3400-\u9fff]/;

// A search for "ark" should find ARK, not "Marks": Latin text matches at the start of a word, Chinese anywhere.
function matches(x: ReferenceItem, q: string): boolean {
  const s = q.trim().toLowerCase();
  if (!s) return true;
  return [x.name.en, x.name.zh, ...x.aliases].some((v) => {
    const low = v.toLowerCase();
    return CJK.test(s) ? low.includes(s) : low.split(/[^a-z0-9&]+/).some((w) => w.startsWith(s)) || low.startsWith(s);
  });
}

export default function Reference() {
  const { t, lang } = useLang();
  const [list, setList] = useState<ReferenceItem[]>([]);
  const [id, setId] = useState("berkshire");
  const [q, setQ] = useState("");
  const [cikIn, setCikIn] = useState("");
  const [data, setData] = useState<ReferenceProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [single, setSingle] = useState("");
  const [top3, setTop3] = useState("");

  useEffect(() => { api.referenceList().then(setList).catch(() => setList([])); }, []);
  useEffect(() => {
    setLoading(true); setError(null); setData(null);
    const req = id.startsWith("cik") ? api.referenceByCik(id.slice(3), lang) : api.reference(id, lang);
    req.then((d) => {
      setData(d);
      setSingle(String(Math.round(d.draft_values.single_max * 100)));
      setTop3(String(Math.round(d.draft_values.top3_max * 100)));
    }).catch((e) => setError(String(e.message ?? e))).finally(() => setLoading(false));
  }, [id, lang]);

  const shown = useMemo(() => list.filter((x) => matches(x, q)), [list, q]);
  const loadCik = (e: FormEvent) => {
    e.preventDefault();
    const v = cikIn.trim().replace(/^0+/, "");
    if (/^\d{1,10}$/.test(v)) { setId(`cik${v}`); setCikIn(""); }
  };

  const frac = (v: string) => Math.min(1, Math.max(0.05, (Number(v) || 0) / 100));
  const draft = useMemo(() => {
    if (!data) return "";
    return data.rule_draft
      .replace(/(SINGLE_MAX_WEIGHT_INVESTED[^\n]*?max: )[0-9.]+/, `$1${frac(single).toFixed(2)}`)
      .replace(/(TOP3_MAX_WEIGHT_INVESTED[^\n]*?max: )[0-9.]+/, `$1${frac(top3).toFixed(2)}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, single, top3]);
  const copy = () => {
    navigator.clipboard?.writeText(draft).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }).catch(() => {});
  };
  const max = data ? Math.max(...data.top.map((x) => x.weight), 0.0001) : 1;
  const p1 = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <section>
      <h1>{t.refTitle}</h1>
      <p className="lede">{t.refLede}</p>
      <p className="notice small">{t.refLimits}</p>

      <div className="toolbar">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.refSearchPh} aria-label={t.refSearch} size={28} />
        <form className="inline" onSubmit={loadCik}>
          <input value={cikIn} onChange={(e) => setCikIn(e.target.value)} placeholder={t.refCikPh} aria-label={t.refCik} inputMode="numeric" maxLength={10} size={14} />{" "}
          <button type="submit" className="secondary">{t.refCikGo}</button>
        </form>
      </div>
      <div className="spanbar wrapbar" role="group" aria-label={t.refPick}>
        {shown.map((x) => (
          <button key={x.id} type="button" className={id === x.id ? "on" : ""} aria-pressed={id === x.id} onClick={() => setId(x.id)}>
            {x.name[lang]}
          </button>
        ))}
        {id.startsWith("cik") && data && <button type="button" className="on" aria-pressed="true">{data.filer}</button>}
      </div>
      {shown.length === 0 && <p className="muted small">{t.refNoMatch}{" "}
        <a href="https://www.sec.gov/search-filings/cik-lookup" target="_blank" rel="noreferrer">{t.refFindCik} ↗</a></p>}

      {loading && <p className="muted">{t.refLoading}</p>}
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <h2 className="h2">{data.name}</h2>
          {data.about && <p>{data.about}</p>}
          {data.custom && <p className="muted small">{t.refCustomNote}</p>}
          <p className="muted small">
            {t.refFiler(data.filer, data.period, data.filed)}{" "}
            <a href={data.filing_url} target="_blank" rel="noreferrer">{t.refFiling} ↗</a>
          </p>
          <ul className="refstats">
            <li><span className="muted small">{t.refTotal}</span><b>{usd(data.total_value_usd)}</b></li>
            <li><span className="muted small">{t.refPositions}</span><b>{data.concentration.positions}</b></li>
            <li><span className="muted small">{t.refTop(1)}</span><b>{p1(data.concentration.top1)}</b></li>
            <li><span className="muted small">{t.refTop(3)}</span><b>{p1(data.concentration.top3)}</b></li>
            <li><span className="muted small">{t.refTop(5)}</span><b>{p1(data.concentration.top5)}</b></li>
            <li><span className="muted small">{t.refTop(10)}</span><b>{p1(data.concentration.top10)}</b></li>
          </ul>
          {data.option_lines_excluded > 0 && <p className="muted small">{t.refOptions(data.option_lines_excluded)}</p>}

          <h3>{t.refHoldings}</h3>
          <div className="scroll">
            <table className="reftable">
              <thead><tr><th>{t.refIssuer}</th><th>{t.refValue}</th><th>{t.refWeight}</th></tr></thead>
              <tbody>
                {data.top.map((x) => (
                  <tr key={x.issuer}>
                    <th>{x.issuer}</th>
                    <td>{usd(x.value_usd)}</td>
                    <td className="barcell"><span className="bar" style={{ width: `${(x.weight / max) * 100}%` }} /><span className="barnum">{p1(x.weight)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.changes && (
            <>
              <h3>{t.refChanges(data.previous_period ?? "", data.period)}</h3>
              <p className="muted small">{t.refChangesNote}</p>
              <div className="refchanges">
                {KINDS.map((k) => (
                  <div key={k}>
                    <h4>{t.refKinds[k]}</h4>
                    {data.changes![k].length === 0 ? <p className="muted small">–</p> : (
                      <ul>
                        {data.changes![k].map((c) => (
                          <li key={c.issuer + c.title_class}>
                            <b>{c.issuer}</b> <span className="muted small">{c.title_class}</span><br />
                            <span className="small">
                              {k === "exited" ? t.refWas(pct(c.weight_before)) :
                                k === "new" ? t.refNow(c.weight_after < 0.0005 ? "<0.1%" : p1(c.weight_after)) :
                                  `${p1(c.weight_before)} → ${p1(c.weight_after)}${c.shares_change_pct == null ? "" : ` (${t.refShares(c.shares_change_pct)})`}`}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          <h2 className="h2">{t.refOwnTitle}</h2>
          <p className="muted small">{t.refOwnNote}</p>
          {data.sources.length > 0 ? (
            <ul className="refsources">
              {data.sources.map((s) => (
                <li key={s.url}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a> <span className="muted small">{t.refKindsSrc[s.kind] ?? s.kind}</span></li>
              ))}
            </ul>
          ) : <p className="muted small">{data.note ?? t.refNoSources}</p>}

          <h2 className="h2">{t.refDraftTitle}</h2>
          <ol className="refsteps">{t.refSteps.map((s) => <li key={s}>{s}</li>)}</ol>
          <div className="toolbar">
            <label>{t.refSingleLabel} ≤ <input type="number" min={5} max={100} step={1} value={single} onChange={(e) => setSingle(e.target.value)} className="narrow" /> %</label>
            <label>{t.refTop3Label} ≤ <input type="number" min={5} max={100} step={1} value={top3} onChange={(e) => setTop3(e.target.value)} className="narrow" /> %</label>
          </div>
          <pre className="ruledraft">{draft}</pre>
          <p className="toolbar">
            <button type="button" className="secondary" onClick={copy}>{copied ? t.mmCopied : t.refCopy}</button>
            <button type="button" className="primary" onClick={() => navigate("/lab/gate", { single: frac(single).toFixed(2), top3: frac(top3).toFixed(2) })}>{t.refTry} →</button>
          </p>
          <p className="muted small">{t.refDraftNote}</p>
        </>
      )}
    </section>
  );
}
