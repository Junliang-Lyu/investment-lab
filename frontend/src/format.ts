export function usd(v: number): string {
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  return `${s}$${a.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
export const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
export const metric = (v: number, unit: "USD" | "ratio") => (unit === "ratio" ? pct(v) : usd(v));
