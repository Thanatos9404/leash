import { ArrowRight, Bot, Check, ExternalLink, FileSignature, KeyRound, Plus, ScrollText, Sparkles, TriangleAlert, Wallet } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Panel, Segmented } from "../../kit";
import { cn } from "../../kit/lib/cn";
import { api, type Mandate, type MandateDetail, type Merchant, type Purchase } from "../../lib/api";
import { ago, dateShort, money, short } from "../../lib/format";
import { linkTo, navigate, useLocation } from "../../lib/router";
import { signJws } from "../../lib/signing";
import { useLeash } from "../store";
import { BusyButton, CartLine, Empty, KeyValue, Meter, OutcomeBadge, SkeletonBlock, StatusBadge, mandateLimit } from "../ui";

export function MandatesScreen() {
  const { dash } = useLeash();
  const { path } = useLocation();
  if (path.endsWith("/new") || (dash && dash.mandates.length === 0)) return <Composer />;
  if (!dash) return <SkeletonBlock className="h-[420px]" />;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-[14px] text-muted">{dash.mandates.length} mandates · newest first</p>
        <Button size="sm" onClick={() => navigate("/app/mandates/new")} icon={<Plus size={15} />}>New mandate</Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {dash.mandates.map((m) => (
          <a key={m.id} href={`/app/mandates/${m.id}`} onClick={linkTo(`/app/mandates/${m.id}`)} className="surface block p-5 transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)]">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-display text-[17px] font-semibold tracking-[-0.01em]">{m.policy.title}</p>
                <p className="text-[12.5px] text-muted">Created {ago(m.created_at)} · until {dateShort(m.policy.valid_until)}</p>
              </div>
              <StatusBadge status={m.status} />
            </div>
            <ul className="mt-4 space-y-1.5">
              {m.plain_language.slice(0, 3).map((l) => (
                <li key={l} className="flex gap-2 text-[13.5px] text-ink-2"><Check size={14} className="mt-0.5 shrink-0 text-ink" />{l}</li>
              ))}
            </ul>
            <div className="mt-5"><Meter used={Number(m.spent)} total={mandateLimit(m)} label="Spent" /></div>
            <div className="mt-3 flex flex-wrap gap-2 text-[12px] text-muted">
              <span>{m.counts.captured ?? 0} paid</span>·<span>{m.counts.blocked ?? 0} blocked</span>·<span>{m.counts.awaiting_approval ?? 0} waiting</span>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

function Composer() {
  const { status, run } = useLeash();
  const [text, setText] = useState("");
  const samples = status?.samples ?? {};
  const labels: Record<string, string> = { coffee: "Coffee restock", gift: "Birthday gift", office: "Office supplies" };
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <Panel className="lg:col-span-8" title="Write a mandate" description="Tell your agent what it may buy, in your own words. You'll review the exact rules before anything is signed.">
        <label htmlFor="mandate-text" className="sr-only">Mandate</label>
        <textarea
          id="mandate-text"
          className="field min-h-[150px] resize-y leading-relaxed"
          placeholder="Keep my coffee stocked. Up to $80 a month, max $30 per order, only from Bean & Bloom…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-[12.5px] text-muted">Try:</span>
          {Object.entries(samples).map(([k, v]) => (
            <button key={k} className="chip-btn" onClick={() => setText(v)}>{labels[k] ?? k}</button>
          ))}
        </div>
        <div className="mt-6 flex justify-end">
          <BusyButton
            label="Compile the rules"
            busyLabel="compile"
            icon={<Sparkles size={16} />}
            disabled={!text.trim()}
            onClick={async () => {
              const m = await run("compile", () => api.compile(text));
              if (m) navigate(`/app/mandates/${m.id}`);
            }}
          />
        </div>
      </Panel>
      <Panel className="lg:col-span-4" title="How Leash reads it">
        <ol className="space-y-4 text-[14px] text-ink-2">
          {[
            ["A model drafts the rules", `${status?.ai === "rules" ? "Offline parser" : (status?.ai ?? "").replace("nebius:", "")} reads your words into a typed policy.`],
            ["Code checks the draft", "Every amount must appear in your text, and every store must be a verified merchant you named."],
            ["You sign the plain-language version", "Those sentences are generated from the rules, not by the model, so what you read is what is enforced."],
            ["PayPal holds the allowance", "You approve one authorization. Leash can never capture more than it."],
          ].map(([t, b], i) => (
            <li key={t} className="flex gap-3">
              <span className="t-num grid size-6 shrink-0 place-items-center rounded-full bg-paper-3 text-[12px]">{i + 1}</span>
              <span><span className="font-medium text-ink">{t}.</span> {b}</span>
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}

export function MandateScreen({ id }: { id: string }) {
  const { dash } = useLeash();
  const [m, setM] = useState<MandateDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.mandate(id).then(setM).catch((e) => setErr(e.message)), [id]);
  useEffect(() => {
    load();
  }, [load, dash]);
  if (err) return <Panel><Empty icon={<ScrollText />} title="Mandate not found" body={err} action={<Button onClick={() => navigate("/app/mandates")}>All mandates</Button>} /></Panel>;
  if (!m) return <SkeletonBlock className="h-[520px]" />;
  return (
    <div className="space-y-4">
      <Header m={m} reload={load} />
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-7">
          <Rules m={m} reload={load} />
          {m.status === "active" && <RunAgent m={m} reload={load} />}
          <Purchases list={m.purchases} />
        </div>
        <div className="space-y-4 lg:col-span-5">
          <Allowance m={m} />
          <Signature m={m} />
          <Timeline m={m} />
        </div>
      </div>
    </div>
  );
}

function Header({ m, reload }: { m: MandateDetail; reload: () => void }) {
  const { run, toast } = useLeash();
  const sign = async () => {
    const res = await run("sign", async () => {
      const { payload } = await api.signingPayload(m.id);
      const token = await signJws(payload, "leash-mandate+jws");
      return api.sign(m.id, token);
    });
    if (res) {
      toast(res.signer.type === "device" ? "Signed with this device's key." : "Signed with the demo custodial key (this browser cannot sign Ed25519).");
      reload();
    }
  };
  const fund = async () => {
    const res = await run("fund", () => api.fund(m.id));
    if (res?.envelope.approve_url) window.location.href = res.envelope.approve_url;
  };
  return (
    <div className="surface flex flex-col gap-4 p-5 md:flex-row md:items-center md:justify-between md:p-6">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="t-h3">{m.policy.title}</h1>
          <StatusBadge status={m.status} />
        </div>
        <p className="mt-1 text-[13.5px] text-muted">
          {m.status === "draft" && "Review the rules below, then sign. Nothing can be spent yet."}
          {m.status === "signed" && "Signed. Approve the allowance on PayPal to switch it on."}
          {m.status === "awaiting_funding" && "Waiting for you to approve the allowance on PayPal."}
          {m.status === "active" && `Live until ${dateShort(m.policy.valid_until)}. Your agent can shop inside these rules.`}
          {m.status === "revoked" && `Revoked ${m.revoked_at ? ago(m.revoked_at) : ""}: ${m.revoke_reason ?? ""}. The PayPal allowance was voided.`}
          {m.status === "expired" && "Expired. Create a new mandate to continue."}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {m.status === "draft" && <BusyButton label="Sign mandate" busyLabel="sign" onClick={sign} icon={<FileSignature size={16} />} />}
        {m.status === "signed" && <BusyButton label="Approve allowance on PayPal" busyLabel="fund" onClick={fund} icon={<Wallet size={16} />} />}
        {m.status === "awaiting_funding" && (
          <>
            <Button href={m.envelope.approve_url} icon={<ExternalLink size={15} />}>Continue on PayPal</Button>
            <BusyButton variant="glass" label="I've approved" busyLabel="refresh" onClick={async () => { await run("refresh", () => api.refresh(m.id)); reload(); }} />
          </>
        )}
        {m.status === "active" && (
          <BusyButton variant="ghost" label="Revoke" busyLabel="revoke" className="!text-accent" onClick={async () => {
            if (!confirm("Revoke this mandate? The PayPal allowance is voided and pending approvals are cancelled.")) return;
            const res = await run("revoke", () => api.revoke(m.id, "Revoked by owner"));
            if (res) { toast(`Revoked. PayPal authorization ${short(res.envelope.authorization_id, 8)} voided.`, "attention"); reload(); }
          }} />
        )}
      </div>
    </div>
  );
}

function Rules({ m, reload }: { m: MandateDetail; reload: () => void }) {
  const draft = m.status === "draft";
  return (
    <Panel title="What your agent may do" description={draft ? "Generated from the rules, not by the model. This is exactly what you sign." : "Enforced on every cart by the policy engine"}
      action={<Badge>{m.compiler.ai === "rules" ? "Parsed offline" : "Drafted by " + m.compiler.ai.replace("nebius:", "").split("/").pop()}</Badge>}>
      <ul className="space-y-2.5">
        {m.plain_language.map((l) => (
          <li key={l} className="flex gap-2.5 text-[14.5px] leading-snug text-ink">
            <span className="mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-full bg-paper-3"><Check size={11} strokeWidth={2.4} /></span>
            {l}
          </li>
        ))}
      </ul>
      {m.compiler.warnings.length > 0 && (
        <div className="mt-5 space-y-2 rounded-2xl bg-accent-soft p-4">
          {m.compiler.warnings.map((w) => (
            <p key={w} className="flex gap-2 text-[13.5px] text-ink"><TriangleAlert size={15} className="mt-0.5 shrink-0 text-accent" />{w}</p>
          ))}
        </div>
      )}
      {draft && <EditRules m={m} reload={reload} />}
      <details className="mt-5 text-[13px] text-muted">
        <summary className="cursor-pointer text-ink-2">Your original words</summary>
        <p className="mt-2 rounded-xl bg-paper-2 p-3 italic">“{m.source_text}”</p>
      </details>
    </Panel>
  );
}

function EditRules({ m, reload }: { m: Mandate; reload: () => void }) {
  const { run } = useLeash();
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [per, setPer] = useState(m.policy.per_purchase_max ?? "");
  const [ask, setAsk] = useState(m.policy.ask_above ?? "");
  const [budget, setBudget] = useState(m.policy.budget?.amount ?? "");
  const [period, setPeriod] = useState<"day" | "week" | "month" | "total">(m.policy.budget?.period ?? "month");
  const [allow, setAllow] = useState<string[]>(m.policy.merchants_allow);
  const [subs, setSubs] = useState(m.policy.allow_subscriptions);
  useEffect(() => {
    api.merchants().then((ms) => setMerchants(ms.filter((x) => x.verified)));
  }, []);
  const save = () => run("edit", () => api.editMandate(m.id, {
    per_purchase_max: per || null, ask_above: ask === "" ? null : ask, budget: budget ? { amount: budget, period } : null,
    merchants_allow: allow, allow_subscriptions: subs,
  })).then((r) => r && reload());
  const num = (label: string, v: string, set: (s: string) => void, hint: string) => (
    <label className="block">
      <span className="text-[12.5px] font-medium text-ink-2">{label}</span>
      <div className="relative mt-1">
        <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted">$</span>
        <input inputMode="decimal" className="field !pl-7" value={v} onChange={(e) => set(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="none" />
      </div>
      <span className="mt-1 block text-[11.5px] text-muted">{hint}</span>
    </label>
  );
  return (
    <div className="mt-6 border-t border-hair pt-5">
      <p className="t-eyebrow mb-3">Adjust before signing</p>
      <div className="grid gap-4 sm:grid-cols-3">
        {num("Per purchase cap", String(per), setPer, "Hard limit")}
        {num("Ask me above", String(ask), setAsk, "0 = approve every purchase")}
        {num("Budget", String(budget), setBudget, "Also the PayPal allowance")}
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <Segmented id="period" value={period} onChange={setPeriod} options={[{ value: "week", label: "Week" }, { value: "month", label: "Month" }, { value: "total", label: "Total" }]} />
        <label className="flex items-center gap-2 text-[13.5px] text-ink-2">
          <input type="checkbox" checked={subs} onChange={(e) => setSubs(e.target.checked)} className="size-4 accent-[var(--ink)]" />
          Allow subscriptions
        </label>
      </div>
      <p className="mt-4 text-[12.5px] font-medium text-ink-2">Allowed stores <span className="font-normal text-muted">(none selected = any verified store)</span></p>
      <div className="mt-2 flex flex-wrap gap-2">
        {merchants.map((x) => {
          const on = allow.includes(x.id);
          return (
            <button key={x.id} onClick={() => setAllow(on ? allow.filter((a) => a !== x.id) : [...allow, x.id])}
              className={cn("chip-btn", on && "!border-ink !bg-ink !text-paper")} aria-pressed={on}>
              {x.name}
            </button>
          );
        })}
      </div>
      <div className="mt-5 flex justify-end"><BusyButton variant="glass" size="sm" label="Save changes" busyLabel="edit" onClick={save} /></div>
    </div>
  );
}

function RunAgent({ m, reload }: { m: Mandate; reload: () => void }) {
  const { run } = useLeash();
  const [task, setTask] = useState("");
  const [last, setLast] = useState<Purchase | null>(null);
  const ideas = m.policy.categories_allow.includes("coffee")
    ? ["Restock my usual 1 kg bag of medium roast whole beans", "A 1 kg bag of Ethiopia Guji from Daily Grind", "Get some decaf for evenings"]
    : m.policy.categories_allow.includes("toys")
      ? ["A puzzle for a 9-year-old who loves maps", "Something creative, like an art set"]
      : ["Two packs of gel pens and sticky notes", "Dotted notebooks for the team"];
  const go = async (t: string) => {
    const p = await run("agent", () => api.runAgent(m.id, t));
    if (p) { setLast(p); reload(); }
  };
  return (
    <Panel title="Send the agent shopping" description="It searches, picks a cart and explains why. Leash decides; the agent can't." action={<Bot size={18} className="text-muted" />}>
      <form onSubmit={(e) => { e.preventDefault(); if (task.trim()) go(task); }} className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="task" className="sr-only">Task</label>
        <input id="task" className="field" value={task} onChange={(e) => setTask(e.target.value)} placeholder="What should it buy?" />
        <BusyButton label="Run agent" busyLabel="agent" onClick={() => task.trim() && go(task)} icon={<ArrowRight size={16} />} />
      </form>
      <div className="mt-3 flex flex-wrap gap-2">
        {ideas.map((i) => <button key={i} className="chip-btn" onClick={() => { setTask(i); go(i); }}>{i}</button>)}
      </div>
      {last && (
        <div className={cn("mt-5 rounded-2xl border p-4", last.decision.outcome === "allow" ? "border-hair" : "border-[color-mix(in_oklab,var(--accent)_35%,transparent)]")}>
          <div className="mb-2 flex items-center justify-between gap-2"><OutcomeBadge outcome={last.decision.outcome} /><StatusBadge status={last.status} /></div>
          <CartLine p={last} />
          <p className="mt-2 text-[13.5px] text-ink-2">{last.decision.summary}</p>
          <a href={`/app/purchases/${last.id}`} onClick={linkTo(`/app/purchases/${last.id}`)} className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-ink hover:underline">
            {last.status === "awaiting_approval" ? "Review and approve" : "Open receipt and checks"} <ArrowRight size={13} />
          </a>
        </div>
      )}
    </Panel>
  );
}

function Purchases({ list }: { list: Purchase[] }) {
  return (
    <Panel title="Carts under this mandate" description="Allowed, asked and blocked">
      {list.length === 0 ? (
        <Empty icon={<Bot />} title="No carts yet" body="When the agent proposes a cart, the decision shows up here." />
      ) : (
        <ul className="divide-y divide-[var(--hair)]">
          {list.map((p) => (
            <li key={p.id}>
              <a href={`/app/purchases/${p.id}`} onClick={linkTo(`/app/purchases/${p.id}`)} className="flex items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="truncate text-[14px] font-medium">{p.cart.items[0]?.name}{p.cart.items[0]?.quantity > 1 ? ` × ${p.cart.items[0].quantity}` : ""}</p>
                  <p className="truncate text-[12.5px] text-muted">{p.cart.merchant_name} · {p.decision.summary}</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <span className="t-num text-[15px]">{money(p.cart.total)}</span>
                  <StatusBadge status={p.status} />
                </div>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Allowance({ m }: { m: Mandate }) {
  const e = m.envelope;
  const left = m.allowance_left !== null ? Number(m.allowance_left) : null;
  return (
    <Panel title="PayPal allowance" description="An Orders v2 AUTHORIZE hold. Each purchase is a partial capture." action={<Wallet size={18} className="text-muted" />}>
      {!e.order_id ? (
        <p className="text-[13.5px] text-muted">Created when you approve the mandate on PayPal. Amount: {money(mandateLimit(m))}.</p>
      ) : (
        <>
          <div className="flex items-end justify-between">
            <div>
              <p className="text-[12.5px] text-muted">Left to spend</p>
              <p className="t-num text-[30px] leading-tight">{left === null ? "–" : money(left)}</p>
            </div>
            <Badge tone={e.authorization_status === "VOIDED" ? "neutral" : e.authorization_id ? "pos" : "accent"}>{e.authorization_status ?? e.order_status ?? "pending"}</Badge>
          </div>
          <div className="mt-4"><Meter used={Number(e.captured)} total={Number(e.amount)} label="Captured" /></div>
          <div className="mt-4">
            <KeyValue rows={[
              ["Order", <span className="mono">{e.order_id}</span>],
              ["Authorization", <span className="mono">{e.authorization_id ?? "after approval"}</span>],
              ["Expires", e.expires_at ? dateShort(e.expires_at) : "29 days after approval"],
            ]} />
          </div>
        </>
      )}
    </Panel>
  );
}

function Signature({ m }: { m: Mandate }) {
  return (
    <Panel title="Signature" description="Ed25519, compact JWS" action={<KeyRound size={18} className="text-muted" />}>
      {m.status === "draft" ? (
        <p className="text-[13.5px] text-muted">Not signed yet. When you sign, this device's key (or the labelled demo key) signs the rules hash.</p>
      ) : (
        <KeyValue rows={[
          ["Status", m.signature_ok ? <Badge tone="pos">Verified</Badge> : <Badge tone="accent">{m.signature_detail}</Badge>],
          ["Signed with", m.signer.type === "device" ? "This device's key" : "Demo custodial key"],
          ["Key id", <span className="mono">{m.signer.kid}</span>],
          ["Rules hash", <span className="mono" title={m.policy_hash}>{short(m.policy_hash, 16)}</span>],
          ["Token", <span className="mono" title="First characters of the signed JWS">{short(m.token_preview, 22)}</span>],
        ]} />
      )}
    </Panel>
  );
}

function Timeline({ m }: { m: MandateDetail }) {
  return (
    <Panel title="Timeline" description="From the hash-chained audit log">
      <ol className="relative space-y-3 border-l border-hair pl-4">
        {m.timeline.slice(-12).reverse().map((e) => (
          <li key={e.seq} className="relative">
            <span className={cn("absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-surface", e.kind.includes("deny") || e.kind.includes("revoked") ? "bg-accent" : "bg-ink")} />
            <p className="text-[13.5px] leading-snug text-ink">{e.title}</p>
            <p className="text-[11.5px] text-muted">{ago(e.ts)} · {e.actor}</p>
          </li>
        ))}
      </ol>
    </Panel>
  );
}
