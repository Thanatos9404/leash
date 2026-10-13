import { Activity, Bot, Building2, Landmark, Link2, Scale, User } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Panel, Segmented } from "../../kit";
import { cn } from "../../kit/lib/cn";
import { api, type LedgerEntry } from "../../lib/api";
import { ago, short } from "../../lib/format";
import { useLeash } from "../store";
import { BusyButton, Empty, SkeletonBlock } from "../ui";

type Filter = "all" | "agent" | "policy" | "paypal" | "you";

function kindOf(actor: string): Filter {
  if (actor.startsWith("agent")) return "agent";
  if (actor === "policy") return "policy";
  if (actor.startsWith("paypal") || actor.startsWith("merchant")) return "paypal";
  if (actor.startsWith("human")) return "you";
  return "all";
}

const ICON: Record<string, JSX.Element> = { agent: <Bot />, policy: <Scale />, paypal: <Landmark />, you: <User />, all: <Activity /> };

export function ActivityScreen() {
  const { dash, run } = useLeash();
  const [rows, setRows] = useState<LedgerEntry[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [chain, setChain] = useState<{ ok: boolean; entries: number; head?: string; broken_at?: number } | null>(null);
  useEffect(() => {
    api.ledger().then(setRows).catch(() => setRows([]));
  }, [dash]);
  if (!rows) return <SkeletonBlock className="h-[520px]" />;
  const shown = rows.filter((r) => filter === "all" || kindOf(r.actor) === filter).slice().reverse();
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <Panel className="lg:col-span-8" title="Audit trail" description="Append-only and hash-chained: each entry carries the SHA-256 of the one before it"
        action={<Segmented id="act" value={filter} onChange={setFilter} options={[{ value: "all", label: "All" }, { value: "agent", label: "Agent" }, { value: "policy", label: "Policy" }, { value: "paypal", label: "PayPal" }, { value: "you", label: "You" }]} />}>
        {shown.length === 0 ? (
          <Empty icon={<Activity />} title="Nothing here yet" body="Compile a mandate to start the trail." />
        ) : (
          <ol className="divide-y divide-[var(--hair)]">
            {shown.map((e) => {
              const k = kindOf(e.actor);
              const bad = e.kind.includes("deny") || e.kind.includes("revoked") || e.kind.includes("refused") || e.kind.includes("kill_switch.on");
              return (
                <li key={e.seq} className="flex gap-3 py-3">
                  <span className={cn("mt-0.5 grid size-8 shrink-0 place-items-center rounded-full border border-hair [&>svg]:size-4 [&>svg]:stroke-[1.7]", bad ? "bg-accent-soft text-accent" : "bg-surface text-ink")}>
                    {e.actor.startsWith("merchant") ? <Building2 /> : ICON[k]}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[14px] leading-snug text-ink">{e.title}</p>
                    <p className="mt-0.5 text-[12px] text-muted">
                      {ago(e.ts)} · {e.actor} · <span className="mono">#{e.seq} {short(e.hash, 10)}</span>
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
      <div className="space-y-4 lg:col-span-4">
        <Panel title="Chain integrity" description="Recomputes every hash from the first entry" action={<Link2 size={18} className="text-muted" />}>
          {chain ? (
            <div className="space-y-2">
              <Badge tone={chain.ok ? "pos" : "accent"}>{chain.ok ? `Intact · ${chain.entries} entries` : `Broken at #${chain.broken_at}`}</Badge>
              {chain.head && <p className="mono break-all text-[11.5px] text-muted">head {chain.head}</p>}
            </div>
          ) : (
            <p className="text-[13.5px] text-muted">Any edit, deletion or reordering of a past entry changes every hash after it.</p>
          )}
          <div className="mt-4"><BusyButton size="sm" variant="glass" label="Verify the chain" busyLabel="chain" onClick={async () => { const r = await run("chain", () => api.verifyChain()); if (r) setChain(r); }} /></div>
        </Panel>
        <Panel title="What gets recorded">
          <ul className="space-y-2 text-[13.5px] text-ink-2">
            <li>Every mandate compile, edit, signature and revocation</li>
            <li>Every agent task and every cart it proposed</li>
            <li>Every policy decision, with the rules that failed</li>
            <li>Every merchant verification and PayPal call, with request ids</li>
          </ul>
        </Panel>
      </div>
    </div>
  );
}
