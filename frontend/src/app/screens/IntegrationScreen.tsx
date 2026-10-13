import { BadgeCheck, Braces, KeyRound, Landmark, RotateCcw, Server } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Panel } from "../../kit";
import { cn } from "../../kit/lib/cn";
import { api, type ReceiptCheck } from "../../lib/api";
import { ago, short } from "../../lib/format";
import { deviceKey, supportsEd25519 } from "../../lib/signing";
import { useLeash } from "../store";
import { BusyButton, KeyValue } from "../ui";

type Call = Awaited<ReturnType<typeof api.paypalCalls>>[number];

export function IntegrationScreen() {
  const { status, run, toast, dash } = useLeash();
  const [calls, setCalls] = useState<Call[]>([]);
  const [kid, setKid] = useState<string | null | undefined>(undefined);
  const [receipt, setReceipt] = useState("");
  const [check, setCheck] = useState<ReceiptCheck | null>(null);
  useEffect(() => {
    api.paypalCalls().then((c) => setCalls(c.slice().reverse())).catch(() => setCalls([]));
  }, [dash]);
  useEffect(() => {
    supportsEd25519().then((ok) => (ok ? deviceKey().then((k) => setKid(k?.kid ?? null)) : setKid(null)));
  }, []);
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-5">
        <Panel title="Environment" action={<Server size={18} className="text-muted" />}>
          <KeyValue rows={[
            ["PayPal", status?.paypal_mode === "sandbox" ? <Badge tone="pos">Sandbox REST</Badge> : <Badge tone="accent">Fixture simulator</Badge>],
            ["Agent model", (status?.ai ?? "").replace("nebius:", "") || "–"],
            ["Writer model", (status?.ai_writer ?? "").replace("nebius:", "") || "–"],
            ["Product search", status?.catalog === "channel3" ? "Channel3 API" : "Demo storefronts (fixture)"],
            ["Webhooks", status?.webhook_verification === true ? "Verified by PayPal" : status?.webhook_verification === "fixture" ? "Fixture signature" : "Not configured"],
          ]} />
          {status?.paypal_mode === "fixture" && (
            <p className="mt-4 rounded-xl bg-paper-2 p-3 text-[12.5px] leading-relaxed text-ink-2">
              No sandbox keys are set, so PayPal is simulated in-process with documented response shapes and ids starting with FX. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to switch to the real sandbox (see KEYS_NEEDED.md).
            </p>
          )}
        </Panel>
        <Panel title="Your signing key" action={<KeyRound size={18} className="text-muted" />}>
          {kid === undefined ? (
            <p className="text-[13.5px] text-muted">Checking this browser…</p>
          ) : kid ? (
            <KeyValue rows={[["Key", <span className="mono">{kid}</span>], ["Where", "This browser (non-extractable, IndexedDB)"], ["Algorithm", "Ed25519 (EdDSA JWS)"]]} />
          ) : (
            <p className="text-[13.5px] text-ink-2">This browser can't create Ed25519 keys, so signatures use the server's labelled demo key <span className="mono">{status?.custodial_kid}</span>.</p>
          )}
        </Panel>
        <Panel title="For merchants" description="Verify an agent's authority before you fulfil" action={<Braces size={18} className="text-muted" />}>
          <ul className="space-y-2 text-[13.5px] text-ink-2">
            <li><span className="mono text-ink">POST /v1/verify</span> with the authorization receipt, your merchant id and cart hash. Receipts are single use.</li>
            <li><span className="mono text-ink">GET /.well-known/jwks.json</span> to verify receipts offline (Ed25519).</li>
          </ul>
          <div className="mt-4 flex gap-2">
            <Button size="sm" variant="glass" href="/.well-known/jwks.json">Open JWKS</Button>
            <Button size="sm" variant="ghost" href="/docs">API reference</Button>
          </div>
        </Panel>
        {status?.paypal_mode === "fixture" && (
          <Panel title="Demo data">
            <BusyButton size="sm" variant="ghost" label="Reset everything" busyLabel="reset" icon={<RotateCcw size={14} />} onClick={async () => {
              if (!confirm("Delete all demo mandates, purchases and the audit trail?")) return;
              if (await run("reset", () => api.reset())) toast("Demo data reset.");
            }} />
          </Panel>
        )}
      </div>
      <div className="space-y-4 lg:col-span-7">
        <Panel title="Check a receipt" description="Paste any Leash intent receipt (compact JWS)">
          <label htmlFor="rcpt" className="sr-only">Receipt</label>
          <textarea id="rcpt" className="field mono min-h-[96px] !text-[12px]" value={receipt} onChange={(e) => setReceipt(e.target.value)} placeholder="eyJhbGciOiJFZERTQSIs…" />
          <div className="mt-3 flex justify-end">
            <BusyButton size="sm" label="Verify" busyLabel="inspect" disabled={!receipt.trim()} onClick={async () => { const r = await run("inspect", () => api.inspectReceipt(receipt)); if (r) setCheck(r); }} />
          </div>
          {check && (
            <ul className="mt-3 space-y-1.5 rounded-2xl bg-paper-2 p-3">
              {check.checks.map((c) => (
                <li key={c.check} className="flex gap-2 text-[13px]"><BadgeCheck size={15} className={cn("mt-0.5 shrink-0", c.ok ? "text-pos" : "text-accent")} />{c.detail}</li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="PayPal API calls" description="From this server. Secrets and tokens are never logged." action={<Landmark size={18} className="text-muted" />}>
          {calls.length === 0 ? (
            <p className="text-[13.5px] text-muted">No calls yet.</p>
          ) : (
            <div className="-mx-2 max-h-[420px] overflow-auto">
              <table className="w-full min-w-[560px] text-left text-[12.5px]">
                <thead className="sticky top-0 bg-surface text-muted">
                  <tr>{["When", "Call", "HTTP", "PayPal-Request-Id"].map((h) => <th key={h} className="px-2 pb-2 font-medium">{h}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-[var(--hair)]">
                  {calls.slice(0, 80).map((c, i) => (
                    <tr key={i}>
                      <td className="whitespace-nowrap px-2 py-2 text-muted">{ago(c.at)}</td>
                      <td className="mono px-2 py-2 text-ink">{c.method} {c.path.replace(/\/[A-Z0-9-]{12,}/g, (s) => "/" + short(s.slice(1), 8))}</td>
                      <td className="px-2 py-2"><Badge tone={c.status < 300 ? "pos" : c.status < 500 ? "accent" : "neg"}>{c.status}</Badge></td>
                      <td className="mono px-2 py-2 text-ink-2">{c.request_id || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
