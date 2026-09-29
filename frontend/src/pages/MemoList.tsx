import { useEffect, useState } from "react";
import { api, ApiError, LabMemo } from "../api";
import { navigate } from "../App";
import { useLang } from "../i18n";
import { forgetMemo, savedMemos } from "../memos";

type Row = { id: string; ticker: string; thesis: string; at: string; memo?: LabMemo | null };

export default function MemoList() {
  const { t } = useLang();
  const [rows, setRows] = useState<Row[]>(() => savedMemos().slice(0, 30));

  useEffect(() => {
    rows.forEach((r) => {
      api.memo(r.id).then((memo) => setRows((xs) => xs.map((x) => (x.id === r.id ? { ...x, memo } : x))))
        .catch((e) => {
          if (e instanceof ApiError && e.status === 404) {
            forgetMemo(r.id);
            setRows((xs) => xs.map((x) => (x.id === r.id ? { ...x, memo: null } : x)));
          }
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const status = (m: LabMemo) => (m.decision ? t.mmDecisions[m.decision] : t.mmSteps[Math.min(
    { skeptic_done: 1, user_responded: 2, reviewed: 3, final: 4 }[m.status as string] ?? 1, 4)]);

  return (
    <section>
      <h1>{t.mlTitle}</h1>
      <p className="lede">{t.mlLede}</p>
      {rows.length === 0 ? (
        <p className="notice">{t.mlEmpty}</p>
      ) : (
        <ul className="memocards">
          {rows.map((r) => (
            <li key={r.id}>
              <a href={`/lab/memo/${r.id}`} onClick={(e) => { e.preventDefault(); if (r.memo !== null) navigate(`/lab/memo/${r.id}`); }}
                 className={r.memo === null ? "gone" : ""}>
                <span className="head"><b>{r.ticker}</b>
                  {r.memo && <span className={`ctype ${r.memo.stance === "short" ? "to_verify" : "fact"}`}>{t.stance[r.memo.stance === "short" ? "short" : "long"]}</span>}
                  <span className="muted small">{r.memo === null ? t.mlGone : r.memo ? status(r.memo) : "…"}</span>
                </span>
                <span className="small">{r.thesis}</span>
                <span className="muted small">{(r.memo?.updated_at ?? r.at).slice(0, 10)}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
