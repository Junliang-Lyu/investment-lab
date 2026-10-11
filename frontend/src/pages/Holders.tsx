import { useEffect, useState } from "react";
import { api, Holder, Holders } from "../api";
import { navigate } from "../App";
import { usd } from "../format";
import { useLang } from "../i18n";

/** Which of the listed institutions held the company in their latest 13F. Context only. */
export default function HoldersView({ ticker }: { ticker: string }) {
  const { t, lang } = useLang();
  const [data, setData] = useState<Holders | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    setData(null); setError(false);
    api.holders(ticker, lang).then(setData).catch(() => setError(true));
  }, [ticker, lang]);

  if (error) return <div className="insiders"><h3>{t.hdTitle}</h3><p className="muted small">{t.hdErr}</p></div>;
  if (!data) return <div className="insiders"><h3>{t.hdTitle}</h3><p className="muted small">{t.hdLoading}</p></div>;
  const change = (h: Holder) => {
    switch (h.change) {
      case "new": return t.hdNew;
      case "exited": return t.hdExited;
      case "increased": return t.hdUp(h.change_pct);
      case "decreased": return t.hdDown(h.change_pct);
      case "unchanged": return t.hdUnchanged;
      default: return "–";
    }
  };
  return (
    <div className="insiders">
      <h3>{t.hdTitle}</h3>
      <p className="muted small">{t.hdNote}</p>
      {data.institutions.length === 0 ? <p className="muted">{t.hdNone(data.covered - data.unavailable.length)}</p> : (
        <div className="scroll">
          <table className="instbl">
            <thead>
              <tr><th>{t.hdInst}</th><th className="r">{t.hdValue}</th><th className="r">{t.hdWeight}</th><th className="r">{t.hdRank}</th>
                <th>{t.hdChange}</th><th>{t.hdPeriod}</th></tr>
            </thead>
            <tbody>
              {data.institutions.map((h) => (
                <tr key={h.id}>
                  <td>
                    <a href="#" title={t.hdOpen} onClick={(e) => { e.preventDefault(); navigate("/lab/reference", { id: h.id }); }}>{h.name}</a>
                  </td>
                  <td className="r">{h.value_usd == null ? "–" : usd(h.value_usd)}</td>
                  <td className="r">{h.weight == null ? "–" : `${(h.weight * 100).toFixed(1)}%`}</td>
                  <td className="r">{h.rank == null ? "–" : t.hdRankOf(h.rank, h.positions)}</td>
                  <td>{change(h)}</td>
                  <td>{h.period}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">
        {data.not_held.length > 0 && <>{t.hdNotHeld} {data.not_held.map((x) => x.name).join(lang === "zh" ? "、" : ", ")}. </>}
        {data.unavailable.length > 0 && <>{t.hdUnavail} {data.unavailable.map((x) => x.name).join(lang === "zh" ? "、" : ", ")}.</>}
      </p>
    </div>
  );
}
