import { useState } from "react";
import { navigate } from "../App";
import { useLang } from "../i18n";
import { savedMemos } from "../memos";

// Where each workflow step starts. Steps 3-5 happen inside a memo, so they point to the skeptic (step 2) that creates one.
const STEP_ROUTE = ["/lab/company", "/lab/skeptic", "/lab/skeptic", "/lab/skeptic", "/lab/skeptic", "/lab/gate"];

export default function Home() {
  const { t } = useLang();
  const [memos] = useState(savedMemos);
  return (
    <section>
      <h1>{t.homeTitle}</h1>
      <p className="lede">{t.homeLede}</p>

      <h2 className="h2">{t.wfTitle}</h2>
      <p className="muted">{t.wfLede}</p>
      <ol className="workflow">
        {t.wfSteps.map(([head, body], i) => (
          <li key={head}>
            <button className="step" onClick={() => navigate(STEP_ROUTE[i])}>
              <span className="num">{i + 1}</span>
              <span><b>{head}</b><br /><span className="muted small">{body}</span></span>
            </button>
          </li>
        ))}
      </ol>
      <p><button type="button" className="primary" onClick={() => navigate("/lab/skeptic")}>{t.wfStart} →</button></p>

      {memos.length > 0 && (
        <>
          <h3>{t.myMemos}</h3>
          <ul className="memolist">
            {memos.map((m) => (
              <li key={m.id}>
                <a href={`/lab/memo/${m.id}`} onClick={(e) => { e.preventDefault(); navigate(`/lab/memo/${m.id}`); }}>
                  <b>{m.ticker}</b> {m.thesis}
                </a>
                <span className="muted small"> · {m.at.slice(0, 10)}</span>
              </li>
            ))}
          </ul>
          <p className="muted small">{t.myMemosNote}</p>
        </>
      )}

      <h3>{t.howTitle}</h3>
      <ul className="how">
        {[t.how1, t.how2, t.how3].map(([head, body]) => <li key={head}><b>{head}</b> {body}</li>)}
      </ul>
    </section>
  );
}
