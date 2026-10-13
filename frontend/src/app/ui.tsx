import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, Check, CircleSlash, Hand, Loader2, RefreshCw, X } from "lucide-react";
import type { ReactNode } from "react";
import { Badge, Button } from "../kit";
import { cn } from "../kit/lib/cn";
import { EASE } from "../kit/lib/motion";
import type { Check as RuleCheck, Mandate, Purchase } from "../lib/api";
import { STATUS_LABEL, money, ruleLabel, tone } from "../lib/format";
import { useLeash } from "./store";

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={tone(status)}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function OutcomeBadge({ outcome }: { outcome: "allow" | "ask" | "deny" }) {
  if (outcome === "allow") return <Badge tone="pos"><Check size={13} strokeWidth={2} /> Allowed</Badge>;
  if (outcome === "ask") return <Badge tone="accent"><Hand size={13} strokeWidth={2} /> Ask first</Badge>;
  return <Badge tone="accent"><CircleSlash size={13} strokeWidth={2} /> Blocked</Badge>;
}

/** Spend against a limit. The fill turns accent only when the limit is nearly used. */
export function Meter({ used, total, label }: { used: number; total: number; label?: string }) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  return (
    <div>
      {label && (
        <div className="mb-1.5 flex items-baseline justify-between text-[12.5px]">
          <span className="text-muted">{label}</span>
          <span className="t-num text-[13px] text-ink">
            {money(used)} <span className="text-muted">of {money(total)}</span>
          </span>
        </div>
      )}
      <div className="h-2 overflow-hidden rounded-full bg-paper-3" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <motion.div
          className={cn("h-full rounded-full", pct >= 90 ? "bg-accent" : "bg-ink")}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.9, ease: EASE }}
        />
      </div>
    </div>
  );
}

export function mandateLimit(m: Mandate): number {
  return Number(m.policy.budget?.amount ?? m.envelope.amount ?? m.policy.per_purchase_max ?? 0);
}

/** Rule-by-rule result of the deterministic policy engine. Failures first. */
export function CheckList({ checks, compact }: { checks: RuleCheck[]; compact?: boolean }) {
  const sorted = [...checks].sort((a, b) => Number(a.ok) - Number(b.ok));
  const shown = compact ? sorted.filter((c) => !c.ok).concat(sorted.filter((c) => c.ok).slice(0, 2)) : sorted;
  return (
    <ul className="space-y-1.5">
      {shown.map((c) => (
        <li key={c.rule} className="flex items-start gap-2.5 text-[13.5px] leading-snug">
          <span
            className={cn(
              "mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-full",
              c.ok ? "bg-[color-mix(in_oklab,var(--pos)_12%,transparent)] text-pos" : c.severity === "ask" ? "bg-paper-3 text-ink" : "bg-accent-soft text-accent",
            )}
          >
            {c.ok ? <Check size={11} strokeWidth={2.4} /> : c.severity === "ask" ? <Hand size={10} strokeWidth={2.2} /> : <X size={11} strokeWidth={2.4} />}
          </span>
          <span className={cn(c.ok ? "text-ink-2" : "text-ink")}>
            <span className="font-medium capitalize">{ruleLabel(c.rule)}</span>
            <span className="text-muted"> · </span>
            {c.detail}
          </span>
        </li>
      ))}
      {compact && sorted.length > shown.length && <li className="pl-7 text-[12.5px] text-muted">+ {sorted.length - shown.length} more rules passed</li>}
    </ul>
  );
}

export function CartLine({ p }: { p: Purchase }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <div className="min-w-0">
        <p className="truncate font-display text-[15px] font-semibold text-ink">
          {p.cart.items.map((i) => `${i.quantity > 1 ? `${i.quantity} × ` : ""}${i.name}`).join(", ")}
        </p>
        <p className="text-[13px] text-muted">
          {p.cart.merchant_name} · <span className={cn(!p.merchant_verified && "text-accent")}>{p.cart.merchant_domain}</span>
        </p>
      </div>
      <span className="t-num text-[20px] text-ink">{money(p.cart.total)}</span>
    </div>
  );
}

export function AgentSays({ p }: { p: Purchase }) {
  return (
    <figure className="rounded-2xl bg-paper-2 px-4 py-3">
      <figcaption className="t-eyebrow mb-1 !text-[11px]">Agent's reasoning · {p.agent_ai.replace("nebius:", "")}</figcaption>
      <blockquote className="text-[14px] leading-relaxed text-ink-2">“{p.reasoning}”</blockquote>
    </figure>
  );
}

export function Empty({ icon, title, body, action }: { icon: ReactNode; title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="mb-4 grid size-12 place-items-center rounded-full bg-paper-2 text-ink [&>svg]:size-5 [&>svg]:stroke-[1.6]">{icon}</div>
      <p className="font-display text-[16px] font-semibold text-ink">{title}</p>
      <p className="mt-1 max-w-[42ch] text-[14px] text-muted">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function SkeletonBlock({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="surface flex flex-col items-center gap-3 px-6 py-10 text-center" role="alert">
      <AlertTriangle className="text-accent" size={22} strokeWidth={1.6} />
      <p className="font-display text-[16px] font-semibold">Leash can't reach its server</p>
      <p className="max-w-[48ch] text-[14px] text-muted">{message}. If you are running locally, start the API with <span className="mono">uvicorn leash.app:app</span>.</p>
      <Button variant="glass" size="sm" onClick={onRetry} icon={<RefreshCw size={14} />}>
        Try again
      </Button>
    </div>
  );
}

export function Toasts() {
  const { toasts } = useLeash();
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 md:bottom-6" aria-live="polite">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: 12, filter: "blur(6px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: 8, filter: "blur(4px)" }}
            transition={{ duration: 0.35, ease: EASE }}
            className={cn(
              "pointer-events-auto max-w-[520px] rounded-full px-4 py-2.5 text-[14px] font-medium shadow-[var(--shadow-lift)]",
              t.tone === "attention" ? "bg-accent text-white" : "bg-ink text-paper",
            )}
          >
            {t.text}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function BusyButton({ label, busyLabel, onClick, variant = "primary", size = "md", icon, disabled, className }: {
  label: string; busyLabel: string; onClick: () => void; variant?: "primary" | "glass" | "ghost" | "accent"; size?: "sm" | "md" | "lg";
  icon?: ReactNode; disabled?: boolean; className?: string;
}) {
  const { busy } = useLeash();
  const mine = busy === busyLabel;
  return (
    <Button variant={variant} size={size} onClick={onClick} disabled={disabled || !!busy} className={className}
      icon={mine ? <Loader2 size={15} className="animate-spin" /> : icon}>
      {label}
    </Button>
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="divide-y divide-[var(--hair)]">
      {rows.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-4 py-2.5 text-[13.5px]">
          <dt className="shrink-0 text-muted">{k}</dt>
          <dd className="min-w-0 truncate text-right text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
