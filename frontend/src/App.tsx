import { useEffect, useState } from "react";
import Home from "./pages/Home";
import Company from "./pages/Company";
import Gate from "./pages/Gate";
import Skeptic from "./pages/Skeptic";
import MemoPage from "./pages/Memo";
import MemoList from "./pages/MemoList";
import Market from "./pages/Market";
import Reference from "./pages/Reference";
import { LangProvider, useLang } from "./i18n";

type Route = "/lab" | "/lab/company" | "/lab/gate" | "/lab/skeptic" | "/lab/memo" | "/lab/memos" | "/lab/market" | "/lab/reference";
const ROUTES: Route[] = ["/lab", "/lab/company", "/lab/gate", "/lab/skeptic", "/lab/memos", "/lab/market", "/lab/reference"];
const MEMO = /^\/lab\/memo\/([A-Za-z0-9_-]{20,40})$/;

function current(): { route: Route; memoId?: string } {
  const p = window.location.pathname.replace(/\/$/, "");
  const m = MEMO.exec(p);
  if (m) return { route: "/lab/memo", memoId: m[1] };
  return { route: (ROUTES as string[]).includes(p) ? (p as Route) : "/lab" };
}

/** Go to a Lab page. `params` replace the query string (the language is kept). */
export function navigate(to: string, params?: Record<string, string>) {
  const q = new URLSearchParams(window.location.search);
  const next = new URLSearchParams();
  if (q.get("lang")) next.set("lang", q.get("lang")!);
  Object.entries(params ?? {}).forEach(([k, v]) => next.set(k, v));
  const qs = next.toString();
  window.history.pushState({}, "", to + (qs ? `?${qs}` : ""));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function Shell() {
  const { t, lang, setLang } = useLang();
  const [{ route, memoId }, setRoute] = useState(current());
  useEffect(() => {
    const on = () => setRoute(current());
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  useEffect(() => { window.scrollTo(0, 0); }, [route, memoId]);

  const link = (to: Route, text: string) => (
    <a href={to} className={route === to || (to === "/lab/memos" && route === "/lab/memo") ? "active" : ""} onClick={(e) => { e.preventDefault(); navigate(to); }}>{text}</a>
  );

  return (
    <>
      <header className="top">
        <div className="wrap row">
          <a className="brand" href="/lab" onClick={(e) => { e.preventDefault(); navigate("/lab"); }}>{t.brand}</a>
          <nav>
            {link("/lab/company", t.navSnapshot)}
            {link("/lab/skeptic", t.navSkeptic)}
            {link("/lab/memos", t.navMemos)}
            {link("/lab/reference", t.navReference)}
            {link("/lab/gate", t.navGate)}
            <button className="lang" onClick={() => setLang(lang === "zh" ? "en" : "zh")} aria-label="Switch language">
              {t.switchTo}
            </button>
          </nav>
        </div>
      </header>
      <main className="wrap" key={lang}>
        {route === "/lab" && <Home />}
        {route === "/lab/company" && <Company />}
        {route === "/lab/gate" && <Gate />}
        {route === "/lab/skeptic" && <Skeptic />}
        {route === "/lab/memo" && memoId && <MemoPage id={memoId} />}
        {route === "/lab/memos" && <MemoList />}
        {route === "/lab/market" && <Market />}
        {route === "/lab/reference" && <Reference />}
      </main>
      <footer className="wrap foot">
        {t.footer}{" "}
        <a href={lang === "zh" ? "https://jun-liang-lyu.com/zh/" : "https://jun-liang-lyu.com/en/"}>Junliang Lyu</a>.
      </footer>
    </>
  );
}

export default function App() {
  return <LangProvider><Shell /></LangProvider>;
}
