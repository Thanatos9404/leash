export function money(v: string | number | null | undefined, cents = true): string {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 });
}

export function ago(ts: string | number): string {
  const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function dateShort(ts: string): string {
  return new Date(ts).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function short(id: string | null | undefined, n = 10): string {
  if (!id) return "–";
  return id.length > n + 2 ? id.slice(0, n) + "…" : id;
}

export const STATUS_LABEL: Record<string, string> = {
  blocked: "Blocked",
  awaiting_approval: "Needs you",
  awaiting_paypal: "Approve on PayPal",
  verifying: "Verifying",
  captured: "Paid",
  rejected: "Rejected",
  voided: "Voided",
  failed: "Failed",
  refunded: "Refunded",
  draft: "Draft",
  signed: "Signed",
  awaiting_funding: "Fund on PayPal",
  active: "Active",
  revoked: "Revoked",
  expired: "Expired",
};

export function tone(status: string): "neutral" | "accent" | "pos" | "neg" {
  if (["captured", "active"].includes(status)) return "pos";
  if (["blocked", "awaiting_approval", "awaiting_paypal", "awaiting_funding"].includes(status)) return "accent";
  if (["failed", "revoked", "voided", "rejected"].includes(status)) return "neutral";
  return "neutral";
}

export function ruleLabel(rule: string): string {
  return rule.replace(/_/g, " ").replace(/\bpaypal\b/, "PayPal");
}
