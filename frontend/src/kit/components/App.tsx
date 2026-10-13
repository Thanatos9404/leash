import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { Search } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import { EASE, SPRING_SNAPPY } from "../lib/motion";

type NavItem = { id: string; label: string; icon: ReactNode; badge?: string };

/**
 * Product shell in the obliqq dashboard style: warm paper sidebar, a soft
 * greeting bar, and white cards on paper. Use it for every app screen.
 */
export function AppShell({
  brand,
  nav,
  active,
  onNavigate,
  greeting,
  subtitle,
  actions,
  footer,
  children,
}: {
  brand: ReactNode;
  nav: { section?: string; items: NavItem[] }[];
  active: string;
  onNavigate: (id: string) => void;
  greeting: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-[100dvh] bg-paper text-ink">
      <aside className="sticky top-0 hidden h-[100dvh] w-[248px] shrink-0 flex-col border-r border-hair bg-surface/70 px-3 py-4 md:flex">
        <div className="px-3 pb-6 pt-1">{brand}</div>
        <LayoutGroup id="app-nav">
          <nav className="flex-1 space-y-6 overflow-y-auto">
            {nav.map((group, gi) => (
              <div key={gi}>
                {group.section && <p className="t-eyebrow mb-2 px-3 !text-[11px]">{group.section}</p>}
                <ul className="space-y-0.5">
                  {group.items.map((it) => {
                    const on = it.id === active;
                    return (
                      <li key={it.id}>
                        <button
                          onClick={() => onNavigate(it.id)}
                          className={cn(
                            "relative flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left font-display text-[14.5px] transition-colors",
                            on ? "text-ink" : "text-ink-2 hover:text-ink",
                          )}
                        >
                          {on && (
                            <motion.span layoutId="nav-pill" transition={SPRING_SNAPPY} className="absolute inset-0 rounded-xl bg-paper-3" />
                          )}
                          <span className="relative [&>svg]:size-[18px] [&>svg]:stroke-[1.6]">{it.icon}</span>
                          <span className="relative flex-1">{it.label}</span>
                          {it.badge && (
                            <span className="relative rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-accent">{it.badge}</span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
        </LayoutGroup>
        {footer && <div className="mt-4 border-t border-hair pt-4">{footer}</div>}
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center justify-between gap-4 border-b border-hair bg-paper/80 px-5 py-4 backdrop-blur-xl md:px-8">
          <div>
            <p className="font-display text-[19px] font-semibold tracking-[-0.02em]">{greeting}</p>
            {subtitle && <p className="text-[13px] text-muted">{subtitle}</p>}
          </div>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
        <AnimatePresence mode="wait">
          <motion.main
            key={active}
            initial={{ opacity: 0, y: 10, filter: "blur(6px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
            transition={{ duration: 0.45, ease: EASE }}
            className="px-5 py-6 md:px-8 md:py-8"
          >
            {children}
          </motion.main>
        </AnimatePresence>
      </div>
    </div>
  );
}

export function SearchPill({ placeholder = "Search" }: { placeholder?: string }) {
  return (
    <label className="hidden h-10 w-[260px] items-center gap-2 rounded-full border border-hair bg-surface px-4 text-[14px] text-muted shadow-[var(--shadow-soft)] focus-within:border-[var(--hair-strong)] lg:flex">
      <Search size={16} strokeWidth={1.7} />
      <input placeholder={placeholder} className="w-full bg-transparent text-ink outline-none placeholder:text-muted" />
    </label>
  );
}

/** obliqq stat tile: icon chip, label, big number, delta chip. Real numbers only. */
export function StatTile({
  icon,
  label,
  value,
  delta,
  tone = "neutral",
  hint,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  delta?: string;
  tone?: "pos" | "neg" | "neutral";
  hint?: string;
}) {
  const toneCls = tone === "pos" ? "text-pos bg-[color-mix(in_oklab,var(--pos)_10%,transparent)]" : tone === "neg" ? "text-neg bg-[color-mix(in_oklab,var(--neg)_10%,transparent)]" : "text-muted bg-[var(--hair)]";
  return (
    <motion.div
      whileHover={{ y: -2 }}
      transition={SPRING_SNAPPY}
      className="relative overflow-hidden rounded-[18px] border border-hair bg-gradient-to-b from-surface to-paper-2 p-4 shadow-[var(--shadow-soft)]"
    >
      <div className="flex items-center gap-2.5">
        <span className="grid size-8 place-items-center rounded-full border border-hair bg-surface text-ink [&>svg]:size-4 [&>svg]:stroke-[1.7]">{icon}</span>
        <span className="text-[13.5px] font-medium text-ink-2">{label}</span>
      </div>
      <div className="mt-5 flex items-end justify-between gap-2">
        <span className="t-num text-[30px] leading-none text-ink">{value}</span>
        {delta && <span className={cn("rounded-full px-2 py-0.5 text-[12px] font-semibold", toneCls)}>{delta}</span>}
      </div>
      {hint && <p className="mt-2 text-[12px] text-muted">{hint}</p>}
    </motion.div>
  );
}

/** White card with a title row. The workhorse container inside the app. */
export function Panel({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("surface p-5 md:p-6", className)}>
      {(title || action) && (
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            {title && <h3 className="font-display text-[16px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>}
            {description && <p className="mt-1 text-[13px] text-muted">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "accent" | "pos" | "neg" }) {
  const map = {
    neutral: "bg-paper-3 text-ink-2",
    accent: "bg-accent-soft text-accent",
    pos: "text-pos bg-[color-mix(in_oklab,var(--pos)_12%,transparent)]",
    neg: "text-neg bg-[color-mix(in_oklab,var(--neg)_12%,transparent)]",
  } as const;
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12px] font-semibold", map[tone])}>{children}</span>;
}

/** Pill toggle with a sliding thumb (obliqq "Mobile App / Web App"). */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  id = "seg",
}: {
  options: { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  id?: string;
}) {
  return (
    <div className="glass inline-flex rounded-full p-1">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn("relative rounded-full px-4 py-1.5 font-display text-[14px] font-semibold transition-colors", value === o.value ? "text-paper" : "text-ink-2 hover:text-ink")}
        >
          {value === o.value && <motion.span layoutId={`${id}-thumb`} transition={SPRING_SNAPPY} className="absolute inset-0 rounded-full bg-ink" />}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function IconButton({ children, label, onClick }: { children: ReactNode; label: string; onClick?: () => void }) {
  return (
    <button
      aria-label={label}
      onClick={onClick}
      className="grid size-10 place-items-center rounded-full border border-hair bg-surface text-ink-2 shadow-[var(--shadow-soft)] transition hover:-translate-y-px hover:text-ink [&>svg]:size-[18px] [&>svg]:stroke-[1.6]"
    >
      {children}
    </button>
  );
}
