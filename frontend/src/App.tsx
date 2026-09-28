import { useEffect, useState } from "react";
import Home from "./pages/Home";
import Company from "./pages/Company";
import Gate from "./pages/Gate";

type Route = "/lab" | "/lab/company" | "/lab/gate";
const ROUTES: Route[] = ["/lab", "/lab/company", "/lab/gate"];

function current(): Route {
  const p = window.location.pathname.replace(/\/$/, "");
  return (ROUTES as string[]).includes(p) ? (p as Route) : "/lab";
}

export function navigate(to: Route) {
  window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export default function App() {
  const [route, setRoute] = useState<Route>(current());
  useEffect(() => {
    const on = () => setRoute(current());
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  useEffect(() => { window.scrollTo(0, 0); }, [route]);

  const link = (to: Route, text: string) => (
    <a href={to} className={route === to ? "active" : ""} onClick={(e) => { e.preventDefault(); navigate(to); }}>{text}</a>
  );

  return (
    <>
      <header className="top">
        <div className="wrap row">
          <a className="brand" href="/lab" onClick={(e) => { e.preventDefault(); navigate("/lab"); }}>Investment Lab</a>
          <nav>{link("/lab/company", "Financial snapshot")}{link("/lab/gate", "Pre-trade gate")}</nav>
        </div>
      </header>
      <main className="wrap">
        {route === "/lab" && <Home />}
        {route === "/lab/company" && <Company />}
        {route === "/lab/gate" && <Gate />}
      </main>
      <footer className="wrap foot">
        Fictional portfolios and public SEC data only. Not investment advice. Built by{" "}
        <a href="https://jun-liang-lyu.com/en/">Junliang Lyu</a>.
      </footer>
    </>
  );
}
