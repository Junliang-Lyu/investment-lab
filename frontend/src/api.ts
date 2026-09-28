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
  weight_before: number; weight_after: number; rule_set_version: string; items: GateItem[]; memo_status?: string | null;
};
export type Metric = { value: number; unit: "USD" | "ratio"; label: string; derived: boolean; derivation: string | null; source: string | null };
export type Snapshot = {
  ticker: string; company: string; cik: string; note: string;
  quarters: { end: string; fiscal_label: string | null; metrics: Record<string, Metric> }[];
  segments: { metric: string; member: string; value: number; label: string; period_end: string; source: string | null }[];
};

export type SkepticClaim = {
  claim: string; type: "fact" | "inference" | "to_verify"; evidence_refs: string[];
  quotes: { source_id: string; text: string }[]; why_it_matters: string | null; breaks_assumption?: string;
};
export type SkepticResult = {
  thesis_restated: string; bull_case: SkepticClaim[]; bear_case: SkepticClaim[]; weakest_assumption: string;
  invalidation_suggestions: { condition: string; observable_metric: string; threshold: string | null }[];
  verify_questions: { question: string; where_to_check: string }[];
  evidence: Record<string, { label: string; period: string; display: string; derived: boolean; note: string | null; source: string | null }>;
  sources?: Record<string, { document: string; url: string | null }>;
  computed?: string[];
};
export type SkepticResponse =
  | { ok: true; cached: boolean; attempts?: number; result: SkepticResult; model: string | null; prompt_version: string }
  | { ok: false; cached: boolean; attempts: number; checks: { advice: number; ungrounded_numbers: number; other: number } };
export type SkepticStatus =
  | { enabled: false; reason: string }
  | { enabled: true; per_visitor_daily: number; visitor_remaining: number; budget_available: boolean; prompt_version: string };
export type EvalReport = {
  run_at: string; prompt_version: string; model: string | null;
  summary: {
    cases: number; shown_advice: number; injection_leaks: number; fact_restated_fake_numbers: number;
    schema_valid_rate: number; final_ok_rate: number; passed: boolean; by_category: Record<string, { cases: number; final_ok: number }>;
  };
};
export type MemoAnswers = {
  reasons: string[]; target_weight_pct: number | null; responses: Partial<Record<"E1" | "E2" | "E3", string>>;
  invalidation: string[]; review_date: string | null; review_focus: string;
};
export type MemoReview = {
  section_a: { verdict: "pass" | "issues"; issues: string[] };
  responses: { counter: "E1" | "E2" | "E3"; verdict: "refuted" | "risk_accepted" | "not_refuted" | "off_topic"; comment: string }[];
  fact_vs_inference: string;
  section_c: { verdict: "clear" | "needs_revision"; issues: string[] };
  section_d: { verdict: "ok" | "needs_detail"; comment: string };
  summary: string;
};
export type MemoStatus = "idea" | "researching" | "skeptic_done" | "user_responded" | "reviewed" | "final" | "archived";
export type Decision = "watchlist" | "paper" | "eligible_for_gate";
export type LabMemo = {
  id: string; ticker: string; lang: "zh" | "en"; thesis: string; created_at: string; updated_at: string; expires_at: string;
  status: MemoStatus; version: number; decision: Decision | null;
  events: { from: string; to: string; actor: string; at: string; reason: string | null }[];
  skeptic: SkepticResult; skeptic_meta: { model: string | null; prompt_version: string };
  answers: MemoAnswers; missing: string[]; review: MemoReview | null;
  review_meta: { model: string | null; prompt_version: string; at: string } | null;
  review_unresolved: boolean; watch_started: string | null;
};
export type ReviewResponse =
  | { ok: true; cached: boolean; memo: LabMemo }
  | { ok: false; attempts: number; checks: { advice: number; ungrounded_numbers: number; other: number } };
export type CustomPortfolio = { cash: number; positions: { symbol: string; market_value: number; sleeve: "core" | "satellite" }[] };

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) { super(message); this.status = status; this.detail = detail; }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep status text */ }
    const msg = typeof detail === "string" ? detail
      : (detail as { message?: string })?.message ?? JSON.stringify(detail);
    throw new ApiError(res.status, msg, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  demoPortfolios: (lang: string) => call<DemoPortfolio[]>(`/api/lab/demo-portfolios?lang=${lang}`),
  gate: (lang: string, body: { portfolio_id: string; symbol: string; side: "buy" | "sell"; amount_usd: number;
    attestations: Record<string, boolean>; memo_id?: string; custom?: CustomPortfolio }) =>
    call<GateResult>(`/api/lab/gate?lang=${lang}`, { method: "POST", body: JSON.stringify(body) }),
  companies: () => call<{ ticker: string }[]>("/api/lab/companies"),
  snapshot: (ticker: string, lang: string) => call<Snapshot>(`/api/lab/companies/${encodeURIComponent(ticker)}/snapshot?lang=${lang}`),
  theses: (ticker: string, lang: string) =>
    call<{ id: string; angle: string; text: string }[]>(`/api/lab/theses/${encodeURIComponent(ticker)}?lang=${lang}`),
  skepticStatus: () => call<SkepticStatus>("/api/lab/skeptic/status"),
  skeptic: (body: { ticker: string; thesis: string; lang: string }) =>
    call<SkepticResponse>("/api/lab/skeptic", { method: "POST", body: JSON.stringify(body) }),
  evals: () => call<EvalReport>("/api/lab/evals/latest"),
  createMemo: (body: { ticker: string; thesis: string; lang: string }) =>
    call<LabMemo>("/api/lab/memos", { method: "POST", body: JSON.stringify(body) }),
  memo: (id: string) => call<LabMemo>(`/api/lab/memos/${encodeURIComponent(id)}`),
  saveAnswers: (id: string, a: MemoAnswers) =>
    call<LabMemo>(`/api/lab/memos/${encodeURIComponent(id)}/answers`, { method: "PUT", body: JSON.stringify(a) }),
  reviewMemo: (id: string) => call<ReviewResponse>(`/api/lab/memos/${encodeURIComponent(id)}/review`, { method: "POST" }),
  finalizeMemo: (id: string, decision: Decision, reason: string | null) =>
    call<LabMemo>(`/api/lab/memos/${encodeURIComponent(id)}/finalize`, { method: "POST", body: JSON.stringify({ decision, reason }) }),
  reopenMemo: (id: string) => call<LabMemo>(`/api/lab/memos/${encodeURIComponent(id)}/reopen`, { method: "POST" }),
  deleteMemo: (id: string) => call<{ deleted: boolean }>(`/api/lab/memos/${encodeURIComponent(id)}`, { method: "DELETE" }),
  memoMarkdownUrl: (id: string) => `/api/lab/memos/${encodeURIComponent(id)}/markdown`,
};
