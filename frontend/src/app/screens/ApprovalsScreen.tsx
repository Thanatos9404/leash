import { CircleSlash, ExternalLink, FileSignature, Hand, ShieldCheck, X } from "lucide-react";
import { Badge, Button, Panel, Segmented } from "../../kit";
import { useState } from "react";
import { api, type Purchase } from "../../lib/api";
import { ago, money } from "../../lib/format";
import { linkTo } from "../../lib/router";
import { signJws } from "../../lib/signing";
import { useLeash } from "../store";
import { AgentSays, BusyButton, CartLine, CheckList, Empty, OutcomeBadge, SkeletonBlock } from "../ui";

// Rules a human may waive once (via a PayPal-approved one-off). Identity, integrity and replay rules never.
const NEVER_WAIVED = ["merchant_verified", "cart_integrity", "price_check", "replay", "agent_authorized", "mandate_signature", "kill_switch", "mandate_active"];

export function ApprovalsScreen() {
  const { dash } = useLeash();
  const [view, setView] = useState<"waiting" | "blocked">("waiting");
  if (!dash) return <SkeletonBlock className="h-[420px]" />;
  const waiting = dash.purchases.filter((p) => p.status === "awaiting_approval" || p.status === "awaiting_paypal");
  const blocked = dash.purchases.filter((p) => p.status === "blocked");
  const list = view === "waiting" ? waiting : blocked;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented id="appr" value={view} onChange={setView} options={[{ value: "waiting", label: `Needs you (${waiting.length})` }, { value: "blocked", label: `Blocked (${blocked.length})` }]} />
        <p className="text-[13px] text-muted">{view === "waiting" ? "Approving signs this exact cart with your key." : "Blocked by a hard rule. Some can be allowed once, on PayPal."}</p>
      </div>
      {list.length === 0 ? (
        <Panel>
          <Empty icon={view === "waiting" ? <ShieldCheck /> : <CircleSlash />} title={view === "waiting" ? "Nothing needs you" : "Nothing blocked yet"}
            body={view === "waiting" ? "Carts over your ask-first line, or past your order count, wait here for a signed yes." : "Try the red team page to watch attacks get stopped."} />
        </Panel>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">{list.map((p) => <ApprovalCard key={p.id} p={p} />)}</div>
      )}
    </div>
  );
}

export function ApprovalCard({ p }: { p: Purchase }) {
  const { run, toast } = useLeash();
  const waivable = p.status === "blocked" && p.decision.checks.filter((c) => !c.ok).every((c) => !NEVER_WAIVED.includes(c.rule));
  const approve = async () => {
    const res = await run(`approve-${p.id}`, async () => {
      const { payload } = await api.approvalPayload(p.id);
      const token = await signJws(payload, "leash-cart-approval+jws");
      return api.approve(p.id, token);
    });
    if (res) toast(res.status === "captured" ? `Approved and paid: ${money(res.cart.total)} at ${res.cart.merchant_name}.` : `Not paid: ${res.decision.summary}`, res.status === "captured" ? "ok" : "attention");
  };
  const exception = async () => {
    const res = await run(`exc-${p.id}`, () => api.exception(p.id));
    if (res?.payment.approve_url) window.location.href = res.payment.approve_url;
  };
  return (
    <Panel className={p.status === "blocked" ? "" : "ring-1 ring-[color-mix(in_oklab,var(--accent)_25%,transparent)]"}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <OutcomeBadge outcome={p.decision.outcome} />
        <span className="text-[12px] text-muted">{ago(p.created_at)} · <a href={`/app/purchases/${p.id}`} onClick={linkTo(`/app/purchases/${p.id}`)} className="hover:underline">details</a></span>
      </div>
      <CartLine p={p} />
      <p className="mt-3 text-[14px] font-medium text-ink">{p.decision.summary}</p>
      <div className="mt-4"><AgentSays p={p} /></div>
      <div className="mt-4"><CheckList checks={p.decision.checks} compact /></div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {p.status === "awaiting_approval" && (
          <>
            <BusyButton variant="ghost" label="Decline" busyLabel={`rej-${p.id}`} icon={<X size={15} />} onClick={() => run(`rej-${p.id}`, () => api.reject(p.id, "Declined by owner"))} />
            <BusyButton label="Approve and sign" busyLabel={`approve-${p.id}`} icon={<FileSignature size={15} />} onClick={approve} />
          </>
        )}
        {p.status === "awaiting_paypal" && p.payment.approve_url && (
          <>
            <BusyButton variant="ghost" label="Cancel" busyLabel={`rej-${p.id}`} onClick={() => run(`rej-${p.id}`, () => api.reject(p.id, "Cancelled by owner"))} />
            <Button href={p.payment.approve_url} icon={<ExternalLink size={15} />}>Approve on PayPal</Button>
          </>
        )}
        {p.status === "blocked" && (waivable ? (
          <BusyButton variant="glass" label="Allow once via PayPal" busyLabel={`exc-${p.id}`} icon={<Hand size={15} />} onClick={exception} />
        ) : (
          <Badge>Cannot be waived: it protects you from spoofing or tampering</Badge>
        ))}
      </div>
    </Panel>
  );
}
