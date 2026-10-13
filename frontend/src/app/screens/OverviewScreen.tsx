import { ArrowRight, Ban, CircleDollarSign, Hand, ScrollText, ShieldCheck, Wallet } from "lucide-react";
import { useMemo } from "react";
import { Badge, Button, Panel, StatTile } from "../../kit";
import type { Purchase } from "../../lib/api";
import { ago, money } from "../../lib/format";
import { linkTo, navigate } from "../../lib/router";
import { useLeash } from "../store";
import { CartLine, Empty, Meter, OutcomeBadge, SkeletonBlock, StatusBadge, mandateLimit } from "../ui";

export function OverviewScreen() {
  const { dash, loading } = useLeash();
  if (loading || !dash) return <OverviewSkeleton />;
  const s = dash.stats;
  const live = dash.mandates.filter((m) => ["active", "awaiting_funding", "signed", "draft"].includes(m.status));
  const pending = dash.purchases.filter((p) => p.status === "awaiting_approval" || p.status === "awaiting_paypal");
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile icon={<ScrollText />} label="Live mandates" value={s.active_mandates} hint="Signed and funded on PayPal" />
        <StatTile icon={<CircleDollarSign />} label="Agent spend, 30 days" value={money(s.spent_30d)} delta={`${s.purchases_30d} paid`} hint="Captured on PayPal" />
        <StatTile icon={<Ban />} label="Blocked attempts" value={s.blocked_30d} hint="Stopped by your rules, last 30 days" />
        <StatTile icon={<Wallet />} label="Held by PayPal" value={money(s.held)} hint="Unspent allowance across live mandates" />
      </div>

      {dash.mandates.length === 0 ? (
        <Panel>
          <Empty
            icon={<ScrollText />}
            title="Give your agent its first mandate"
            body="Describe what it may buy in plain words. Leash turns that into rules you sign, and PayPal holds the allowance."
            action={<Button onClick={() => navigate("/app/mandates/new")} icon={<ArrowRight size={16} />}>Write a mandate</Button>}
          />
        </Panel>
      ) : (
        <div className="grid gap-4 lg:grid-cols-12">
          <Panel className="lg:col-span-8" title="Mandates" description="What each agent may spend, and how much is left" action={<a href="/app/mandates" onClick={linkTo("/app/mandates")} className="text-[13px] font-medium text-ink-2 hover:text-ink">All mandates</a>}>
            <ul className="divide-y divide-[var(--hair)]">
              {live.concat(dash.mandates.filter((m) => !live.includes(m))).slice(0, 5).map((m) => (
                <li key={m.id}>
                  <a href={`/app/mandates/${m.id}`} onClick={linkTo(`/app/mandates/${m.id}`)} className="group grid gap-3 py-4 sm:grid-cols-[1fr_220px] sm:items-center">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="truncate font-display text-[15.5px] font-semibold text-ink group-hover:underline">{m.policy.title}</p>
                        <StatusBadge status={m.status} />
                      </div>
                      <p className="mt-0.5 truncate text-[13px] text-muted">{m.plain_language[0]}</p>
                    </div>
                    <Meter used={Number(m.spent)} total={mandateLimit(m)} label={m.policy.budget ? `${m.policy.budget.period === "total" ? "Total" : "This " + m.policy.budget.period}` : "Allowance"} />
                  </a>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel className="lg:col-span-4" title="Needs you" description="Carts your rules asked you to approve" action={pending.length ? <Badge tone="accent">{pending.length}</Badge> : undefined}>
            {pending.length === 0 ? (
              <Empty icon={<ShieldCheck />} title="Nothing waiting" body="Anything over your ask-first line will appear here." />
            ) : (
              <ul className="space-y-3">
                {pending.slice(0, 4).map((p) => (
                  <li key={p.id}>
                    <a href="/app/approvals" onClick={linkTo("/app/approvals")} className="block rounded-2xl border border-hair p-3.5 transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-soft)]">
                      <CartLine p={p} />
                      <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-accent">
                        <Hand size={13} /> {p.decision.summary.replace(/^Needs your OK\. /, "")}
                      </p>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        <Panel className="lg:col-span-7" title="Agent spend" description="Captured on PayPal per day, last 14 days">
          <SpendChart purchases={dash.purchases} />
        </Panel>
        <Panel className="lg:col-span-5" title="Latest decisions" description="Every cart is checked before money moves">
          <RecentDecisions purchases={dash.purchases.slice(0, 6)} />
        </Panel>
      </div>
    </div>
  );
}

function RecentDecisions({ purchases }: { purchases: Purchase[] }) {
  if (!purchases.length) return <Empty icon={<ShieldCheck />} title="No carts yet" body="Run the agent from a mandate to see decisions here." />;
  return (
    <ul className="divide-y divide-[var(--hair)]">
      {purchases.map((p) => (
        <li key={p.id}>
          <a href={`/app/purchases/${p.id}`} onClick={linkTo(`/app/purchases/${p.id}`)} className="flex items-center justify-between gap-3 py-3">
            <div className="min-w-0">
              <p className="truncate text-[14px] font-medium text-ink">{p.cart.merchant_name} · {money(p.cart.total)}</p>
              <p className="truncate text-[12.5px] text-muted">{p.decision.summary}</p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <OutcomeBadge outcome={p.decision.outcome} />
              <span className="text-[11.5px] text-muted">{ago(p.created_at)}</span>
            </div>
          </a>
        </li>
      ))}
    </ul>
  );
}

/** Custom SVG bars: hairline grid, rounded tops, accent fill fading 20% to 0 on hover only. */
function SpendChart({ purchases }: { purchases: Purchase[] }) {
  const days = useMemo(() => {
    const out: { key: string; label: string; total: number }[] = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000);
      out.push({ key: d.toISOString().slice(0, 10), label: d.toLocaleDateString(undefined, { day: "numeric", month: "short" }), total: 0 });
    }
    for (const p of purchases) {
      if (p.status !== "captured" && p.status !== "refunded") continue;
      const k = (p.payment.captured_at ?? p.created_at).slice(0, 10);
      const d = out.find((x) => x.key === k);
      if (d) d.total += Number(p.cart.total);
    }
    return out;
  }, [purchases]);
  const max = Math.max(10, ...days.map((d) => d.total));
  const W = 560, H = 180, gap = 8, bw = (W - gap * 13) / 14;
  if (!days.some((d) => d.total > 0)) return <Empty icon={<CircleDollarSign />} title="No captures yet" body="Paid purchases appear here, one bar per day." />;
  return (
    <svg viewBox={`0 0 ${W} ${H + 24}`} className="w-full" role="img" aria-label="Agent spend per day over the last 14 days">
      <defs>
        <linearGradient id="bar" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--sky-top)" />
          <stop offset="100%" stopColor="color-mix(in oklab, var(--sky-top) 35%, white)" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75, 1].map((t) => (
        <line key={t} x1="0" x2={W} y1={H - H * t} y2={H - H * t} stroke="var(--hair)" />
      ))}
      {days.map((d, i) => {
        const h = Math.max(d.total > 0 ? 4 : 0, (d.total / max) * (H - 8));
        return (
          <g key={d.key}>
            <title>{`${d.label}: ${money(d.total)}`}</title>
            <rect x={i * (bw + gap)} y={H - h} width={bw} height={h} rx={6} fill="url(#bar)" className="transition-opacity hover:opacity-80" />
            {(i % 3 === 0 || i === 13) && (
              <text x={i * (bw + gap) + bw / 2} y={H + 18} textAnchor="middle" fontSize="11" fill="var(--muted)">
                {d.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function OverviewSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <SkeletonBlock key={i} className="h-[126px]" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-12">
        <SkeletonBlock className="h-[300px] lg:col-span-8" />
        <SkeletonBlock className="h-[300px] lg:col-span-4" />
      </div>
    </div>
  );
}
