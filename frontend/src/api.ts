export type Violation = { rule_code: string; severity: "info" | "warn" | "break"; message: string; symbol?: string | null };
export type Position = { symbol: string; name: string; market_value: number; weight: number; sleeve: string };
export type DemoPortfolio = {
  id: string; name: string; description: string; price_date: string; net_liquidation: number; cash: number;
  rule_set: string; positions: Position[]; findings: Violation[];
};
export type GateItem = {
  key: string; section: string; label: string; kind: "auto" | "self_attest";
  status: "pass" | "fail" | "unknown" | "not_applicable"; severity: "info" | "warn" | "break"; detail: string;
};
export type GateResult = {
  overall: "clear" | "incomplete" | "warnings" | "rule_breaks"; symbol: string; side: string; amount_usd: number;
  weight_before: number; weight_after: number; rule_set_version: string; items: GateItem[];
};
export type Metric = { value: number; unit: "USD" | "ratio"; label: string; derived: boolean; derivation: string | null; source: string | null };
export type Snapshot = {
  ticker: string; company: string; cik: string; note: string;
  quarters: { end: string; fiscal_label: string | null; metrics: Record<string, Metric> }[];
  segments: { metric: string; member: string; value: number; label: string; period_end: string; source: string | null }[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep status text */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  demoPortfolios: () => request<DemoPortfolio[]>("/api/lab/demo-portfolios"),
  gate: (body: { portfolio_id: string; symbol: string; side: "buy" | "sell"; amount_usd: number; attestations: Record<string, boolean> }) =>
    request<GateResult>("/api/lab/gate", { method: "POST", body: JSON.stringify(body) }),
  companies: () => request<{ ticker: string }[]>("/api/lab/companies"),
  snapshot: (ticker: string) => request<Snapshot>(`/api/lab/companies/${encodeURIComponent(ticker)}/snapshot?lang=en`),
};
