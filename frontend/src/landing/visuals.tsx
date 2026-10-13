// Real app components fed with output captured from the Leash engine (coffee mandate, offline run).
import { BadgeCheck, CircleDollarSign, FileSignature, Gauge, Hand, ScrollText, ShieldCheck, Wallet } from "lucide-react";
import { Badge, Wordmark } from "../kit";
import { cn } from "../kit/lib/cn";
import type { Check } from "../lib/api";
import { CheckList, Meter } from "../app/ui";

export const RULES = [
  "Never spends more than $30.00 on a single purchase.",
  "Asks you first before any purchase over $25.00.",
  "Spends at most $80.00 per month.",
  "Buys only from Bean & Bloom Coffee, Daily Grind Roasters, Northside Pantry. Every other store is blocked.",
  "Never signs you up for subscriptions or recurring charges.",
  "Paid from a $80.00 PayPal allowance you approve once. Leash can never capture more than that.",
];

export const SPOOF_CHECKS: Check[] = [
  { rule: "merchant_verified", ok: false, severity: "deny", detail: "Possible spoof: this store uses the name 'Bean & Bloom Coffee' but its domain beanandbl00m.example is not the verified beanandbloom.example." },
  { rule: "mandate_signature", ok: true, severity: "deny", detail: "Your signature on the mandate verifies." },
  { rule: "per_purchase_max", ok: true, severity: "deny", detail: "$14.99 is within the $30.00 per-purchase cap." },
  { rule: "budget", ok: true, severity: "deny", detail: "$14.99 fits the $58.00 left of your $80.00 budget." },
  { rule: "replay", ok: true, severity: "deny", detail: "First use of this cart." },
];

export const INJECTION_CHECKS: Check[] = [
  { rule: "merchant_allowed", ok: false, severity: "deny", detail: "Gold Reserve Roasters is not one of the stores you allowed." },
  { rule: "per_purchase_max", ok: false, severity: "deny", detail: "$2400.00 is over your $30.00 per-purchase cap." },
  { rule: "paypal_allowance", ok: false, severity: "deny", detail: "Only $58.00 is left on the PayPal allowance." },
  { rule: "mandate_signature", ok: true, severity: "deny", detail: "Your signature on the mandate verifies." },
];

export function HeroProduct() {
  return (
    <div className="grid grid-cols-1 text-left md:grid-cols-[200px_1fr]">
      <aside className="hidden border-r border-hair bg-paper-2/60 p-4 md:block">
        <Wordmark name="LEASH" suffix=".pay" />
        <div className="mt-6 space-y-1">
          {[["Overview", <Gauge key="g" />], ["Mandates", <ScrollText key="s" />], ["Approvals", <Hand key="h" />], ["Receipts", <BadgeCheck key="b" />]].map(([l, icon], i) => (
            <div key={String(l)} className={cn("flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-[13px] [&>svg]:size-[15px] [&>svg]:stroke-[1.6]", i === 0 ? "bg-paper-3 text-ink" : "text-ink-2")}>
              {icon}
              {l}
            </div>
          ))}
        </div>
      </aside>
      <div className="p-4 md:p-5">
        <p className="font-display text-[15px] font-semibold">Hello, Yashvardhan</p>
        <p className="text-[12px] text-muted">1 live mandate · $58.00 held by PayPal for your agents</p>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            [<ScrollText key="1" />, "Live mandates", "1"],
            [<CircleDollarSign key="2" />, "Agent spend", "$22.00"],
            [<ShieldCheck key="3" />, "Blocked", "8"],
            [<Wallet key="4" />, "Held by PayPal", "$58.00"],
          ].map(([icon, l, v]) => (
            <div key={String(l)} className="rounded-2xl border border-hair bg-gradient-to-b from-surface to-paper-2 p-3">
              <div className="flex items-center gap-2 text-[12px] text-ink-2 [&>svg]:size-[14px] [&>svg]:stroke-[1.7]">{icon}{l}</div>
              <p className="t-num mt-3 text-[22px]">{v}</p>
            </div>
          ))}
        </div>
        <div className="mt-3 grid gap-3 lg:grid-cols-[1.15fr_1fr]">
          <div className="rounded-2xl border border-hair p-4">
            <div className="flex items-center justify-between">
              <p className="font-display text-[14px] font-semibold">Coffee restock</p>
              <Badge tone="pos">Active</Badge>
            </div>
            <div className="mt-3"><Meter used={22} total={80} label="This month" /></div>
            <ul className="mt-3 space-y-1.5 text-[12.5px] text-ink-2">
              {RULES.slice(0, 3).map((r) => <li key={r} className="truncate">· {r}</li>)}
            </ul>
          </div>
          <div className="rounded-2xl border border-[color-mix(in_oklab,var(--accent)_30%,transparent)] p-4">
            <div className="flex items-center justify-between">
              <p className="font-display text-[14px] font-semibold">House Blend, 1 kg · $14.99</p>
              <Badge tone="accent">Blocked</Badge>
            </div>
            <p className="mt-1 text-[12px] text-muted">beanandbl00m.example</p>
            <div className="mt-3 text-[12px]"><CheckList checks={SPOOF_CHECKS.slice(0, 3)} /></div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function MandateVisual() {
  return (
    <div className="surface p-5 text-left">
      <div className="flex items-center justify-between">
        <p className="font-display text-[15px] font-semibold">Coffee restock</p>
        <Badge tone="pos"><FileSignature size={12} /> Signed</Badge>
      </div>
      <p className="mt-1 text-[12px] text-muted">“Keep my coffee stocked. Up to $80 a month, max $30 per order…”</p>
      <ul className="mt-4 space-y-2">
        {RULES.map((r) => (
          <li key={r} className="rounded-xl bg-paper-2 px-3 py-2 text-[13px] text-ink">{r}</li>
        ))}
      </ul>
      <p className="mono mt-3 text-[11px] text-muted">EdDSA (Ed25519) signature over the SHA-256 rules hash</p>
    </div>
  );
}

export function DecisionVisual() {
  return (
    <div className="surface p-5 text-left">
      <div className="flex items-center justify-between">
        <p className="font-display text-[15px] font-semibold">5 × Panama Geisha tin · $2,400.00</p>
        <Badge tone="accent">Blocked</Badge>
      </div>
      <p className="mt-1 text-[12px] text-muted">Agent: “The product page says the user pre-approved $5,000 here.”</p>
      <div className="mt-4"><CheckList checks={INJECTION_CHECKS} /></div>
    </div>
  );
}

export function ReceiptVisual() {
  return (
    <div className="surface p-5 text-left">
      <div className="flex items-center justify-between">
        <p className="font-display text-[15px] font-semibold">Intent receipt</p>
        <Badge tone="pos"><BadgeCheck size={12} /> Verified</Badge>
      </div>
      <div className="mt-4"><Meter used={22} total={80} label="PayPal allowance captured" /></div>
      <dl className="mt-4 divide-y divide-[var(--hair)] text-[13px]">
        {[
          ["Mandate", "signed, unchanged"],
          ["Merchant check", "beanandbloom.example verified"],
          ["Authorization", "AUTHORIZE · partial capture"],
          ["Chain", "linked to previous receipt"],
        ].map(([k, v]) => (
          <div key={k} className="flex justify-between py-2"><dt className="text-muted">{k}</dt><dd className="text-ink">{v}</dd></div>
        ))}
      </dl>
    </div>
  );
}

/** The flow, drawn as a diagram for the bento. */
export function FlowDiagram() {
  const steps = ["Your words", "Signed rules", "PayPal hold", "Merchant check", "Receipt"];
  return (
    <svg viewBox="0 0 520 96" className="w-full" role="img" aria-label="Your words become signed rules, PayPal holds the allowance, the merchant checks the agent, and a receipt is issued">
      {steps.map((s, i) => {
        const x = 8 + i * 104;
        return (
          <g key={s}>
            <rect x={x} y="22" width="88" height="44" rx="14" fill={i === 2 ? "var(--ink)" : "var(--surface)"} stroke="var(--hair-strong)" />
            <text x={x + 44} y="48" textAnchor="middle" fontSize="11.5" fontFamily="var(--font-display)" fontWeight="600" fill={i === 2 ? "var(--paper)" : "var(--ink)"}>{s}</text>
            {i < steps.length - 1 && <path d={`M${x + 90} 44 h12`} stroke="var(--muted)" strokeWidth="1.4" markerEnd="url(#arr)" />}
          </g>
        );
      })}
      <defs>
        <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L8 4 L0 8 z" fill="var(--muted)" /></marker>
      </defs>
    </svg>
  );
}
