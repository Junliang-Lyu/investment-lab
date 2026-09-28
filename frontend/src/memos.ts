// The visitor's own memo links, remembered in this browser only (the memo itself lives on the server).
export type SavedMemo = { id: string; ticker: string; thesis: string; at: string };
const KEY = "lab-memos";

export function savedMemos(): SavedMemo[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((m) => m && typeof m.id === "string") : [];
  } catch { return []; }
}

export function rememberMemo(m: SavedMemo) {
  try {
    localStorage.setItem(KEY, JSON.stringify([m, ...savedMemos().filter((x) => x.id !== m.id)].slice(0, 50)));
  } catch { /* storage may be blocked */ }
}

export function forgetMemo(id: string) {
  try { localStorage.setItem(KEY, JSON.stringify(savedMemos().filter((x) => x.id !== id))); } catch { /* ignore */ }
}
