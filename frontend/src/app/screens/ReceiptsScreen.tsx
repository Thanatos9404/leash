import { ArrowLeft, BadgeCheck, Download, FileCheck2, FileText, Gavel, RotateCcw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Panel } from "../../kit";
import { cn } from "../../kit/lib/cn";
import { api, type Purchase, type ReceiptCheck } from "../../lib/api";
import { ago, dateShort, money, short } from "../../lib/format";
import { linkTo, navigate, useLocation } from "../../lib/router";
import { useLeash } from "../store";
import { AgentSays, BusyButton, CartLine, CheckList, Empty, KeyValue, OutcomeBadge, SkeletonBlock, StatusBadge } from "../ui";
import { ApprovalCard } from "./ApprovalsScreen";

export function ReceiptsScreen() {
  const { dash } = useLeash();
  const { search } = useLocation();
  const q = new URLSearchParams(search).get("q")?.toLowerCase() ?? "";
  const rows = useMemo(() => {
    const all = (dash?.purchases ?? []).filter((p) => q || ["captured", "refunded"].includes(p.status));
    return q ? all.filter((p) => `${p.id} ${p.cart.merchant_name} ${p.cart.items.map((i) => i.name).join(" ")} ${p.payment.capture_id ?? ""}`.toLowerCase().includes(q)) : all;
  }, [dash, q]);
  if (!dash) return <SkeletonBlock className="h-[420px]" />;
  return (
    <Panel title={q ? `Purchases matching “${q}”` : "Intent receipts"} description="Every paid purchase carries a signed receipt chaining your mandate, the agent's reasoning, the cart and the PayPal capture."
      action={q ? <Button variant="ghost" size="sm" onClick={() => navigate("/app/receipts")}>Clear search</Button> : <Badge>{rows.length} receipts</Badge>}>
      {rows.length === 0 ? (
        <Empty icon={<FileCheck2 />} title={q ? "No matches" : "No receipts yet"} body={q ? "Try a merchant, product or PayPal capture id." : "Receipts are issued when PayPal captures a purchase."} />
      ) : (
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-[13.5px]">
            <thead>
              <tr className="text-[12px] text-muted">
                {["Purchase", "Merchant", "Amount", "PayPal capture", "Receipt", "Status"].map((h) => <th key={h} className="px-2 pb-3 font-medium">{h}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--hair)]">
              {rows.map((p) => (
                <tr key={p.id} className="cursor-pointer transition-colors hover:bg-paper-2" onClick={() => navigate(`/app/purchases/${p.id}`)}>
                  <td className="px-2 py-3">
                    <a href={`/app/purchases/${p.id}`} onClick={linkTo(`/app/purchases/${p.id}`)} className="font-medium text-ink hover:underline">{p.cart.items[0]?.name}</a>
                    <p className="text-[12px] text-muted">{ago(p.created_at)}</p>
                  </td>
                  <td className="px-2 py-3 text-ink-2">{p.cart.merchant_name}</td>
                  <td className="t-num px-2 py-3">{money(p.cart.total)}</td>
                  <td className="mono px-2 py-3 text-ink-2">{short(p.payment.capture_id, 12)}</td>
                  <td className="mono px-2 py-3 text-ink-2">{short(p.receipt_hash, 10)}</td>
                  <td className="px-2 py-3"><StatusBadge status={p.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function PurchaseScreen({ id }: { id: string }) {
  const { dash, run, toast, status } = useLeash();
  const [p, setP] = useState<Purchase | null>(null);
  const [check, setCheck] = useState<ReceiptCheck | null>(null);
  const load = useCallback(() => api.purchase(id).then(setP).catch(() => setP(null)), [id]);
  useEffect(() => {
    load();
  }, [load, dash]);
  if (!p) return <SkeletonBlock className="h-[520px]" />;
  if (p.status === "awaiting_approval" || p.status === "awaiting_paypal" || p.status === "blocked")
    return (
      <div className="space-y-4">
        <Back />
        <div className="max-w-[760px]"><ApprovalCard p={p} /></div>
      </div>
    );
  const verify = async () => {
    if (!p.receipt) return;
    const res = await run("verify", () => api.inspectReceipt(p.receipt!));
    if (res) setCheck(res);
  };
  const payload = p.receipt_payload ?? {};
  return (
    <div className="space-y-4">
      <Back />
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-7">
          <Panel>
            <div className="mb-3 flex flex-wrap items-center gap-2"><OutcomeBadge outcome={p.decision.outcome} /><StatusBadge status={p.status} />
              {p.approval.method && <Badge>{p.approval.method === "auto" ? "Auto-approved by your rules" : p.approval.method === "user-signature" ? "You signed this cart" : "Allowed once via PayPal"}</Badge>}
            </div>
            <CartLine p={p} />
            <p className="mt-2 text-[13px] text-muted">Task: {p.task}</p>
            <div className="mt-4"><AgentSays p={p} /></div>
            {p.error && <p className="mt-3 rounded-xl bg-accent-soft p-3 text-[13.5px] text-ink">{p.error}</p>}
          </Panel>
          <Panel title="Policy decision" description={`${p.decision.checks.filter((c) => c.ok).length} of ${p.decision.checks.length} rules passed · deterministic, no model involved`}>
            <CheckList checks={p.decision.checks} />
          </Panel>
          {p.verification.checks && (
            <Panel title="Merchant verification" description={`Before capture, ${p.verification.verifier} checked the agent's authority against Leash's signed receipt`}>
              <ul className="space-y-1.5">
                {p.verification.checks.map((c) => (
                  <li key={c.check} className="flex gap-2 text-[13.5px]"><BadgeCheck size={16} className={cn("mt-0.5 shrink-0", c.ok ? "text-pos" : "text-accent")} /><span><span className="font-medium capitalize">{c.check.replace(/_/g, " ")}</span> · {c.detail}</span></li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
        <div className="space-y-4 lg:col-span-5">
          <Panel title="PayPal" description={status?.paypal_mode === "sandbox" ? "Sandbox objects" : "Fixture objects (simulated, ids start with FX)"}>
            <KeyValue rows={[
              ["Mode", p.payment.mode === "exception" ? "One-off authorization" : "Allowance (partial capture)"],
              ["Order", <span className="mono">{p.payment.order_id ?? "–"}</span>],
              ["Authorization", <span className="mono">{p.payment.authorization_id ?? "–"}</span>],
              ["Capture", <span className="mono">{p.payment.capture_id ?? "–"}</span>],
              ["PayPal fee", p.payment.paypal_fee ? money(p.payment.paypal_fee) : "–"],
              ["Refunded", p.refunds.length ? money(p.refunds.reduce((s, r) => s + Number(r.amount), 0)) : "–"],
            ]} />
            {p.status === "captured" && (
              <div className="mt-4 flex flex-wrap gap-2">
                <BusyButton variant="glass" size="sm" label="Refund" busyLabel="refund" icon={<RotateCcw size={14} />} onClick={async () => {
                  const amt = prompt(`Refund how much? Up to ${money(p.cart.total)}`, p.cart.total);
                  if (!amt) return;
                  const r = await run("refund", () => api.refund(p.id, amt, "Refund requested by owner"));
                  if (r) { toast(`Refunded ${money(amt)} through PayPal.`); setP(r); }
                }} />
              </div>
            )}
          </Panel>
          {p.receipt && (
            <Panel title="Intent receipt" description="Compact JWS, Ed25519. Exportable as dispute evidence." action={<FileText size={18} className="text-muted" />}>
              <KeyValue rows={[
                ["Receipt hash", <span className="mono" title={p.receipt_hash ?? ""}>{short(p.receipt_hash, 16)}</span>],
                ["Chained to", <span className="mono">{payload.prev ? short(payload.prev, 16) : "first in chain"}</span>],
                ["Mandate", <a href={`/app/mandates/${p.mandate_id}`} onClick={linkTo(`/app/mandates/${p.mandate_id}`)} className="mono hover:underline">{p.mandate_id}</a>],
                ["Issued", payload.iat ? dateShort(new Date(payload.iat * 1000).toISOString()) : "–"],
              ]} />
              <div className="mt-4 flex flex-wrap gap-2">
                <BusyButton size="sm" label="Verify receipt" busyLabel="verify" icon={<ShieldCheck size={14} />} onClick={verify} />
                <Button size="sm" variant="glass" href={`/api/purchases/${p.id}/evidence.pdf`} icon={<Download size={14} />}>Evidence PDF</Button>
                <Button size="sm" variant="ghost" href={`/api/purchases/${p.id}/evidence.json`}>JSON bundle</Button>
              </div>
              {check && (
                <ul className="mt-4 space-y-1.5 rounded-2xl bg-paper-2 p-3">
                  {check.checks.map((c) => (
                    <li key={c.check} className="flex gap-2 text-[13px]"><BadgeCheck size={15} className={cn("mt-0.5 shrink-0", c.ok ? "text-pos" : "text-accent")} />{c.detail}</li>
                  ))}
                </ul>
              )}
              <details className="mt-4 text-[12.5px]">
                <summary className="cursor-pointer text-ink-2">Decoded payload</summary>
                <pre className="mono mt-2 max-h-[320px] overflow-auto rounded-xl bg-paper-2 p-3 text-[11.5px] leading-relaxed text-ink-2">{JSON.stringify(payload, null, 2)}</pre>
              </details>
            </Panel>
          )}
          {p.payment.capture_id && <DisputePanel p={p} onChange={setP} />}
        </div>
      </div>
    </div>
  );
}

function DisputePanel({ p, onChange }: { p: Purchase; onChange: (p: Purchase) => void }) {
  const { run, toast, status } = useLeash();
  const d = p.dispute;
  return (
    <Panel title="Disputes" description="If the buyer claims the agent wasn't authorised, the receipt is the evidence." action={<Gavel size={18} className="text-muted" />}>
      {!d.id ? (
        <div className="space-y-3">
          <p className="text-[13.5px] text-ink-2">No dispute on this capture.</p>
          {status?.paypal_mode === "fixture" ? (
            <BusyButton variant="glass" size="sm" label="Simulate a buyer dispute" busyLabel="dispute" onClick={async () => {
              const r = await run("dispute", () => api.simulateDispute(p.id));
              if (r) onChange(r);
            }} />
          ) : (
            <p className="text-[12.5px] text-muted">In the sandbox, open a dispute as the buyer on sandbox.paypal.com; Leash links it by capture id.</p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <KeyValue rows={[["Dispute", <span className="mono">{d.id}</span>], ["Reason", d.reason ?? "–"], ["Status", <Badge tone={d.status === "UNDER_REVIEW" ? "pos" : "accent"}>{d.status}</Badge>], ["Amount", money(d.amount)]]} />
          {d.evidence_submitted ? (
            <p className="text-[13px] text-ink-2">Signed receipt submitted {ago(d.evidence_submitted)} through the Disputes API{d.statement_ai && d.statement_ai !== "template" ? `, statement drafted by ${d.statement_ai.replace("nebius:", "").split("/").pop()}` : ""}.</p>
          ) : (
            <BusyButton size="sm" label="Respond with the signed receipt" busyLabel="respond" icon={<Gavel size={14} />} onClick={async () => {
              const r = await run("respond", () => api.respondDispute(p.id));
              if (r) { toast("Evidence submitted to PayPal: statement plus the signed receipt PDF."); onChange(r); }
            }} />
          )}
          {d.simulated && <p className="text-[11.5px] text-muted">Simulated dispute (fixture mode).</p>}
        </div>
      )}
    </Panel>
  );
}

function Back() {
  return (
    <button onClick={() => history.length > 1 ? history.back() : navigate("/app/receipts")} className="inline-flex items-center gap-1.5 text-[13.5px] font-medium text-ink-2 hover:text-ink">
      <ArrowLeft size={15} /> Back
    </button>
  );
}
