import { motion, useMotionTemplate, useMotionValue, useReducedMotion, useSpring } from "motion/react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import { Reveal, Stagger, StaggerItem } from "./Reveal";
import { SkyBackdrop } from "./SkyBackdrop";

/** Mono eyebrow, display headline, optional lead. Centered or left. */
export function SectionHeader({
  eyebrow,
  title,
  lead,
  align = "center",
  className,
}: {
  eyebrow?: string;
  title: ReactNode;
  lead?: ReactNode;
  align?: "center" | "left";
  className?: string;
}) {
  return (
    <Reveal className={cn(align === "center" ? "mx-auto text-center" : "", "max-w-[720px]", className)}>
      {eyebrow && <p className="t-eyebrow mb-4">{eyebrow}</p>}
      <h2 className="t-h2">{title}</h2>
      {lead && <p className={cn("t-lead mt-5", align === "center" && "mx-auto max-w-[56ch]")}>{lead}</p>}
    </Reveal>
  );
}

/**
 * Copy on one side, a product visual on a sky tile on the other. Alternate
 * `flip` between consecutive splits.
 */
export function FeatureSplit({
  eyebrow,
  title,
  body,
  bullets,
  action,
  visual,
  flip,
}: {
  eyebrow: string;
  title: ReactNode;
  body: ReactNode;
  bullets?: { icon: ReactNode; label: string }[];
  action?: ReactNode;
  visual: ReactNode;
  flip?: boolean;
}) {
  return (
    <div className="grid items-center gap-10 md:grid-cols-2 md:gap-16">
      <Reveal className={cn(flip && "md:order-2")}>
        <p className="t-eyebrow mb-4">{eyebrow}</p>
        <h3 className="t-h2 !text-[clamp(30px,3.4vw,46px)]">{title}</h3>
        <div className="t-lead mt-5 max-w-[46ch]">{body}</div>
        {action && <div className="mt-8">{action}</div>}
        {bullets && (
          <div className="mt-8 grid grid-cols-2 gap-2.5">
            {bullets.map((b) => (
              <div key={b.label} className="flex items-center gap-2.5 rounded-full border border-hair bg-surface px-3.5 py-2.5 text-[14px] font-medium text-ink-2">
                <span className="text-ink">{b.icon}</span>
                {b.label}
              </div>
            ))}
          </div>
        )}
      </Reveal>
      <Reveal delay={0.1} className={cn(flip && "md:order-1")}>
        <div className="relative isolate overflow-hidden rounded-[28px] p-6 md:p-10">
          <SkyBackdrop className="-z-10" clouds={false} />
          <motion.div whileHover={{ y: -4 }} transition={{ type: "spring", stiffness: 260, damping: 24 }}>
            {visual}
          </motion.div>
        </div>
      </Reveal>
    </div>
  );
}

/** 12-column bento. Each BentoCard takes `span` (cols on desktop). */
export function Bento({ children, className }: { children: ReactNode; className?: string }) {
  return <Stagger className={cn("grid grid-cols-1 gap-4 md:grid-cols-12", className)}>{children}</Stagger>;
}

/** Paper card with a cursor spotlight and a subtle 3D lean. */
export function BentoCard({
  span = 4,
  icon,
  title,
  body,
  visual,
  className,
}: {
  span?: 4 | 5 | 6 | 7 | 8 | 12;
  icon?: ReactNode;
  title: ReactNode;
  body: ReactNode;
  visual?: ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const mx = useMotionValue(-200);
  const my = useMotionValue(-200);
  const rx = useSpring(0, { stiffness: 200, damping: 20 });
  const ry = useSpring(0, { stiffness: 200, damping: 20 });
  const spotlight = useMotionTemplate`radial-gradient(420px circle at ${mx}px ${my}px, color-mix(in oklab, var(--accent) 10%, transparent), transparent 60%)`;
  const spanCls: Record<number, string> = {
    4: "md:col-span-4",
    5: "md:col-span-5",
    6: "md:col-span-6",
    7: "md:col-span-7",
    8: "md:col-span-8",
    12: "md:col-span-12",
  };
  return (
    <StaggerItem className={cn(spanCls[span], "[perspective:1200px]")}>
      <motion.article
        style={{ rotateX: rx, rotateY: ry }}
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          mx.set(e.clientX - r.left);
          my.set(e.clientY - r.top);
          if (reduce) return;
          rx.set(((e.clientY - r.top) / r.height - 0.5) * -4);
          ry.set(((e.clientX - r.left) / r.width - 0.5) * 4);
        }}
        onPointerLeave={() => {
          rx.set(0);
          ry.set(0);
          mx.set(-200);
          my.set(-200);
        }}
        className={cn("paper-card group relative flex h-full flex-col overflow-hidden p-6 md:p-7", className)}
      >
        <motion.div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: spotlight }} />
        {visual && <div className="relative mb-6">{visual}</div>}
        {icon && (
          <div className="relative mb-5 grid size-9 place-items-center rounded-full bg-surface text-ink shadow-[var(--shadow-soft)]">{icon}</div>
        )}
        <h3 className="relative font-display text-[17px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
        <p className="relative mt-2 text-[14.5px] leading-relaxed text-ink-2">{body}</p>
      </motion.article>
    </StaggerItem>
  );
}

/** Infinite horizontal strip for the honest "built with" row (tech, datasets, APIs). */
export function Marquee({ items, speed = 40 }: { items: ReactNode[]; speed?: number }) {
  const reduce = useReducedMotion();
  const row = (
    <div className="flex shrink-0 items-center gap-14 pr-14">
      {items.map((it, i) => (
        <div key={i} className="shrink-0 font-display text-[17px] font-semibold tracking-[-0.01em] text-muted transition-colors hover:text-ink">
          {it}
        </div>
      ))}
    </div>
  );
  return (
    <div className="fade-x overflow-hidden">
      <motion.div
        className="flex w-max"
        animate={reduce ? undefined : { x: ["0%", "-50%"] }}
        transition={{ duration: speed, ease: "linear", repeat: Infinity }}
      >
        {row}
        {row}
      </motion.div>
    </div>
  );
}

/** Closing call to action on the sky, followed by the footer card. */
export function CTASection({ title, lead, action }: { title: ReactNode; lead?: ReactNode; action: ReactNode }) {
  return (
    <section className="relative isolate overflow-hidden pb-10 pt-32">
      <SkyBackdrop className="-z-10" />
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-32 bg-gradient-to-b from-paper to-transparent" />
      <div className="container-x">
        <Reveal className="mx-auto max-w-[680px] text-center">
          <h2 className="t-h2">{title}</h2>
          {lead && <p className="t-lead mt-4">{lead}</p>}
          <div className="mt-8 flex justify-center">{action}</div>
        </Reveal>
      </div>
    </section>
  );
}

export function FooterCard({
  brand,
  blurb,
  columns,
  legal,
}: {
  brand: ReactNode;
  blurb: ReactNode;
  columns: { title: string; links: { label: string; href: string }[] }[];
  legal: ReactNode;
}) {
  return (
    <div className="relative isolate -mt-1 overflow-hidden pb-10" style={{ background: "var(--sky-bottom)" }}>
      <footer className="container-x">
        <div className="glass rounded-[28px] p-8 md:p-10">
          <div className="grid gap-10 md:grid-cols-[1.4fr_1fr_1fr]">
            <div>
              {brand}
              <p className="mt-4 max-w-[34ch] text-[14.5px] leading-relaxed text-ink-2">{blurb}</p>
            </div>
            {columns.map((c) => (
              <div key={c.title}>
                <p className="t-eyebrow mb-4 !text-ink">{c.title}</p>
                <ul className="space-y-2.5">
                  {c.links.map((l) => (
                    <li key={l.label}>
                      <a href={l.href} className="text-[14.5px] text-ink-2 transition-colors hover:text-ink">
                        {l.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="mt-10 border-t border-hair pt-6 text-[13px] text-muted">{legal}</div>
        </div>
      </footer>
    </div>
  );
}
