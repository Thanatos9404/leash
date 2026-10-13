// Typed client for the Leash FastAPI backend. Same origin in production; proxied by Vite in development.

export type Severity = "deny" | "ask";
export type Check = { rule: string; ok: boolean; severity: Severity; detail: string };
export type Decision = { outcome: "allow" | "ask" | "deny"; checks: Check[]; summary: string; evaluated_at: string };

export type CartItem = { product_id: string; name: string; quantity: number; unit_price: string; category: string; is_subscription: boolean; item_url: string };
export type Cart = {
  id: string; merchant_id: string; merchant_name: string; merchant_domain: string; items: CartItem[];
  subtotal: string; shipping: string; tax: string; total: string; currency: string; nonce: string; created_at: string; cart_hash: string;
};

export type PurchaseStatus = "blocked" | "awaiting_approval" | "awaiting_paypal" | "verifying" | "captured" | "rejected" | "voided" | "failed" | "refunded";
export type VerificationCheck = { check: string; ok: boolean; detail: string };

export type Purchase = {
  id: string; mandate_id: string; agent_id: string; task: string; reasoning: string; agent_ai: string;
  cart: Cart; decision: Decision; status: PurchaseStatus;
  approval: { method?: string; by?: string; at?: string; token_hash?: string; kid?: string };
  payment: { mode?: string; order_id?: string; authorization_id?: string; capture_id?: string; approve_url?: string; paypal_fee?: string; captured_at?: string };
  verification: { ok?: boolean; at?: string; checks?: VerificationCheck[]; verifier?: string };
  receipt?: string | null; receipt_hash?: string | null; receipt_payload?: Record<string, any> | null;
  refunds: { id: string; amount: string; reason: string; status: string; at: string }[];
  dispute: { id?: string; reason?: string; status?: string; amount?: string; simulated?: boolean; evidence_submitted?: string; statement_ai?: string };
  error?: string | null; created_at: string; updated_at: string; merchant_verified: boolean;
};

export type Merchant = { id: string; name: string; domain: string; category: string; verified: boolean; note?: string };

export type Policy = {
  title: string; purpose: string; currency: string;
  per_purchase_max: string | null; ask_above: string | null;
  budget: { amount: string; period: "day" | "week" | "month" | "total" } | null;
  max_purchases: { count: number; period: string } | null; min_interval_minutes: number | null;
  merchants_allow: string[]; unknown_merchant: Severity; categories_allow: string[]; categories_block: string[];
  max_quantity: number | null; allow_subscriptions: boolean; valid_from: string; valid_until: string;
  time_window: { days: number[]; start_hour: number; end_hour: number } | null;
};

export type MandateStatus = "draft" | "signed" | "awaiting_funding" | "active" | "revoked" | "expired";

export type Mandate = {
  id: string; owner: string; agent_id: string; source_text: string; policy: Policy; plain_language: string[]; policy_hash: string;
  compiler: { ai: string; warnings: string[]; unknown_merchants: string[] };
  status: MandateStatus; signer: { kid?: string; type?: string };
  envelope: { order_id?: string; approve_url?: string; order_status?: string; authorization_id?: string; authorization_status?: string; amount: string; captured: string; expires_at?: string };
  created_at: string; activated_at?: string; revoked_at?: string; revoke_reason?: string; receipt_head?: string;
  token_preview: string; signature_ok: boolean; signature_detail: string; spent: string; allowance_left: string | null;
  counts: Record<string, number>; merchants: Merchant[];
};

export type MandateDetail = Mandate & { purchases: Purchase[]; timeline: LedgerEntry[] };

export type LedgerEntry = { seq: number; ts: number; subject: string | null; actor: string; kind: string; title: string; data: Record<string, any>; prev_hash: string; hash: string };

export type Dashboard = {
  owner: string; frozen: boolean;
  stats: { active_mandates: number; spent_30d: string; purchases_30d: number; blocked_30d: number; pending: number; held: string; receipts: number; disputes: number };
  mandates: Mandate[]; purchases: Purchase[]; timeline: LedgerEntry[];
};

export type Status = {
  paypal_mode: "sandbox" | "fixture"; paypal_env: string; ai: string; ai_writer: string; catalog: string; frozen: boolean; owner: string;
  samples: Record<string, string>; scenarios: Record<string, string>; receipt_kid: string; custodial_kid: string;
  llm_usage: { calls: number }; webhook_verification: boolean | string;
};

export type ReceiptCheck = { valid: boolean; checks: VerificationCheck[]; payload: Record<string, any> | null; hash?: string };

export class ApiError extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) throw new ApiError(data?.error || data?.detail || `Request failed (${res.status})`);
  return data as T;
}

export const api = {
  status: () => call<Status>("GET", "/api/status"),
  dashboard: () => call<Dashboard>("GET", "/api/dashboard"),
  merchants: () => call<Merchant[]>("GET", "/api/merchants"),
  compile: (text: string) => call<Mandate>("POST", "/api/mandates", { text }),
  mandate: (id: string) => call<MandateDetail>("GET", `/api/mandates/${id}`),
  editMandate: (id: string, patch: Record<string, unknown>) => call<Mandate>("PATCH", `/api/mandates/${id}`, patch),
  signingPayload: (id: string) => call<{ payload: Record<string, unknown>; typ: string }>("GET", `/api/mandates/${id}/signing-payload`),
  sign: (id: string, token?: string | null) => call<Mandate>("POST", `/api/mandates/${id}/sign`, token ? { token } : {}),
  fund: (id: string) => call<Mandate>("POST", `/api/mandates/${id}/fund`),
  refresh: (id: string) => call<Mandate>("POST", `/api/mandates/${id}/refresh`),
  revoke: (id: string, reason: string) => call<Mandate>("POST", `/api/mandates/${id}/revoke`, { reason }),
  runAgent: (id: string, task: string) => call<Purchase>("POST", `/api/mandates/${id}/agent`, { task }),
  scenario: (id: string, name: string) => call<Purchase>("POST", `/api/mandates/${id}/scenario`, { name }),
  purchase: (id: string) => call<Purchase>("GET", `/api/purchases/${id}`),
  approvalPayload: (id: string) => call<{ typ: string; payload: Record<string, unknown> }>("POST", `/api/purchases/${id}/approval-payload`),
  approve: (id: string, token?: string | null) => call<Purchase>("POST", `/api/purchases/${id}/approve`, token ? { token } : {}),
  reject: (id: string, reason: string) => call<Purchase>("POST", `/api/purchases/${id}/reject`, { reason }),
  exception: (id: string) => call<Purchase>("POST", `/api/purchases/${id}/exception`),
  refund: (id: string, amount: string | null, reason: string) => call<Purchase>("POST", `/api/purchases/${id}/refund`, { amount, reason }),
  simulateDispute: (id: string) => call<Purchase>("POST", `/api/purchases/${id}/dispute/simulate`),
  syncDisputes: () => call<{ linked: { dispute_id: string; purchase: string; status: string }[] }>("POST", "/api/disputes/sync"),
  respondDispute: (id: string) => call<Purchase>("POST", `/api/purchases/${id}/dispute/respond`),
  inspectReceipt: (receipt: string) => call<ReceiptCheck>("POST", "/api/receipts/verify", { receipt }),
  merchantVerify: (receipt: string, merchant_id?: string) => call<{ valid: boolean; checks: VerificationCheck[] }>("POST", "/v1/verify", { receipt, merchant_id, consume: false }),
  killSwitch: (on: boolean) => call<{ frozen: boolean; revoked?: string[] }>("POST", "/api/kill-switch", { on }),
  registerKey: (jwk: Record<string, string>, label: string) => call<{ kid: string }>("POST", "/api/keys", { jwk, label }),
  ledger: (subject?: string) => call<LedgerEntry[]>("GET", `/api/ledger${subject ? `?subject=${subject}` : "?limit=300"}`),
  verifyChain: () => call<{ ok: boolean; entries: number; head?: string; broken_at?: number }>("GET", "/api/ledger/verify"),
  paypalCalls: () => call<{ at: number; method: string; path: string; status: number; request_id: string; debug_id: string; mode: string; ms: number }[]>("GET", "/api/paypal/calls"),
  reset: () => call<{ ok: boolean }>("POST", "/api/demo/reset"),
};
