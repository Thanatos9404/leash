import { Copy, FileWarning, Fingerprint, Gift, Repeat, Scissors, ShieldAlert, Store, TrendingUp, Zap } from "lucide-react";
import { useState } from "react";
import { Button, Panel } from "../../kit";
import { cn } from "../../kit/lib/cn";
import { api, type Purchase } from "../../lib/api";
import { money } from "../../lib/format";
import { linkTo, navigate } from "../../lib/router";
import { useLeash } from "../store";
import { BusyButton, Empty, OutcomeBadge, SkeletonBlock } from "../ui";

const ATTACKS: { id: string; icon: JSX.Element; title: string; story: string }[] = [
  { id: "injection", icon: <FileWarning />, title: "Prompt injection", story: "A product page tells agents the user pre-approved $5,000 and to buy 5 tins." },
  { id: "spoof", icon: <Store />, title: "Lookalike store", story: "Same name, “Bean & Bloom Coffee”, on beanandbl00m.example, at a bait price." },
  { id: "subscription", icon: <Repeat />, title: "Sneaky subscription", story: "The agent picks the auto-renewing plan because it is 10% cheaper." },
  { id: "split", icon: <Scissors />, title: "Split purchase", story: "Two orders at one store within minutes to stay under the per-purchase cap." },
  { id: "over_cap", icon: <TrendingUp />, title: "Over the cap", story: "A bigger bag that costs more than the per-purchase limit." },
  { id: "gift_card", icon: <Gift />, title: "Gift-card cash-out", story: "A gift card: the classic way to turn spending power into cash." },
  { id: "tamper", icon: <Fingerprint />, title: "Tampered cart", story: "The agent submits a price lower than the merchant's listing." },
  { id: "replay", icon: <Copy />, title: "Replay", story: "Resubmit a cart that was already paid, nonce and all." },
];

export function RedTeamScreen() {
  const { dash, run } = useLeash();
  const [results, setResults] = useState<Record<string, Purchase>>({});
  if (!dash) return <SkeletonBlock className="h-[420px]" />;
  const target = dash.mandates.find((m) => m.status === "active");
  if (!target)
    return (
      <Panel>
        <Empty icon={<ShieldAlert />} title="Needs a live mandate" body="Attacks run against a real, funded mandate so you see the same policy engine and PayPal path a real agent hits." action={<Button onClick={() => navigate("/app/mandates/new")}>Create one</Button>} />
      </Panel>
    );
  const fire = async (id: string) => {
    const p = await run(`rt-${id}`, () => api.scenario(target.id, id));
    if (p) setResults((r) => ({ ...r, [id]: p }));
  };
  const all = async () => {
    for (const a of ATTACKS) await fire(a.id);
  };
  return (
    <div className="space-y-4">
      <Panel title="Red team" description={<>Each button replays what a compromised or manipulated agent would submit against <a className="font-medium text-ink hover:underline" href={`/app/mandates/${target.id}`} onClick={linkTo(`/app/mandates/${target.id}`)}>{target.policy.title}</a>. No special cases: the same checkout path as the real agent.</>}
        action={<BusyButton size="sm" label="Run all eight" busyLabel="rt-all" icon={<Zap size={14} />} onClick={() => run("rt-all", all)} />}>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {ATTACKS.map((a) => {
            const r = results[a.id];
            return (
              <div key={a.id} className={cn("flex flex-col rounded-[18px] border p-4 transition", r ? (r.status === "captured" ? "border-hair" : "border-[color-mix(in_oklab,var(--accent)_30%,transparent)] bg-accent-soft/40") : "border-hair bg-paper-2")}>
                <div className="flex items-center justify-between">
                  <span className="grid size-9 place-items-center rounded-full bg-surface text-ink shadow-[var(--shadow-soft)] [&>svg]:size-4 [&>svg]:stroke-[1.7]">{a.icon}</span>
                  {r && <OutcomeBadge outcome={r.decision.outcome} />}
                </div>
                <p className="mt-4 font-display text-[15.5px] font-semibold">{a.title}</p>
                <p className="mt-1 flex-1 text-[13px] leading-relaxed text-ink-2">{a.story}</p>
                {r ? (
                  <a href={`/app/purchases/${r.id}`} onClick={linkTo(`/app/purchases/${r.id}`)} className="mt-3 block text-[12.5px] leading-snug text-ink hover:underline">
                    {r.decision.summary} <span className="text-muted">({money(r.cart.total)})</span>
                  </a>
                ) : (
                  <div className="mt-3"><BusyButton size="sm" variant="glass" label="Run attack" busyLabel={`rt-${a.id}`} onClick={() => fire(a.id)} /></div>
                )}
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}
