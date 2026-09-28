import { navigate } from "../App";

export default function Home() {
  return (
    <section>
      <h1>An investment system that argues back</h1>
      <p className="lede">
        Most tools tell you what to buy. This one does the opposite: it checks a decision against written rules,
        pulls the numbers straight from SEC filings, and makes the case against your idea before you act on it.
      </p>
      <div className="cards">
        <button className="card" onClick={() => navigate("/lab/company")}>
          <h2>Financial snapshot</h2>
          <p>Eight quarters of revenue, margins, cash flow, capex and segment results. Every number links to the filing it came from.</p>
        </button>
        <button className="card" onClick={() => navigate("/lab/gate")}>
          <h2>Pre-trade gate</h2>
          <p>Pick a fictional portfolio, propose a trade, and see every rule and checklist item it passes or fails.</p>
        </button>
      </div>
      <h3>How it works</h3>
      <ul className="how">
        <li><b>Deterministic rules.</b> Position limits and checklists are plain code with versioned rule files, not a model's opinion.</li>
        <li><b>Evidence first.</b> Financial data comes from SEC XBRL filings; quarterly values derived from year-to-date totals are marked and explained.</li>
        <li><b>AI as skeptic, with guardrails.</b> In the full workflow the model writes counter-arguments; every number and quote it uses is checked against the evidence, and advice is blocked.</li>
      </ul>
    </section>
  );
}
