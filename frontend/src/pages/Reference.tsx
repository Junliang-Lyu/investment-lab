import { useEffect, useState } from "react";
import { api, ReferenceItem, ReferenceProfile } from "../api";
import { pct, usd } from "../format";
import { useLang } from "../i18n";

const KINDS = ["new", "exited", "increased", "decreased"] as const;

export default function Reference() {
  const { t, lang } = useLang();
  const [list, setList] = useState<ReferenceItem[] | null>(null);
  const [id, setId] = useState("berkshire");
  const [data, setData] = useState<ReferenceProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => { api.referenceList().then(setList).catch(() => setList([])); }, []);
  useEffect(() => {
    setLoading(true); setError(null); setData(null);
    api.reference(id, lang).then(setData).catch((e) => setError(String(e.message ?? e))).finally(() => setLoading(false));
  }, [id, lang]);

  const copy = () => {
    if (!data) return;
    navigator.clipboard?.writeText(data.rule_draft).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }).catch(() => {});
  };
  const max = data ? Math.max(...data.top.map((x) => x.weight), 0.0001) : 1;
  const p1 = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <section>
      <h1>{t.refTitle}</h1>
      <p className="lede">{t.refLede}</p>
      <p className="notice small">{t.refLimits}</p>

      <div className="spanbar" role="group" aria-label={t.refPick}>
        {(list ?? []).map((x) => (
          <button key={x.id} type="button" className={id === x.id ? "on" : ""} aria-pressed={id === x.id} onClick={() => setId(x.id)}>
            {x.name[lang]}
          </button>
        ))}
      </div>

      {loading && <p className="muted">{t.refLoading}</p>}
      {error && <p className="error">{error}</p>}
      {data && (
        <>
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

          <h2 className="h2">{t.refHoldings}</h2>
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
              <h2 className="h2">{t.refChanges(data.previous_period ?? "", data.period)}</h2>
              <p className="muted small">{t.refChangesNote}</p>
              <div className="refchanges">
                {KINDS.map((k) => (
                  <div key={k}>
                    <h3>{t.refKinds[k]}</h3>
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

          <h2 className="h2">{t.refDraftTitle}</h2>
          <p className="muted small">{t.refDraftNote}</p>
          <pre className="ruledraft">{data.rule_draft}</pre>
          <p><button type="button" className="secondary" onClick={copy}>{copied ? t.mmCopied : t.refCopy}</button></p>
        </>
      )}
    </section>
  );
}
