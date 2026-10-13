import { AnimatePresence, motion, useMotionValueEvent, useScroll } from "motion/react";
import { Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "../lib/cn";
import { EASE } from "../lib/motion";
import { Button } from "./Button";

type Link = { label: string; href: string };

/**
 * Full-width bar at the top that condenses into a floating glass pill once the
 * page scrolls, exactly like obliqq.
 */
export function Nav({
  brand,
  links,
  cta,
}: {
  brand: ReactNode;
  links: Link[];
  cta: { label: string; href: string };
}) {
  const { scrollY } = useScroll();
  const [docked, setDocked] = useState(false);
  const [open, setOpen] = useState(false);
  useMotionValueEvent(scrollY, "change", (v) => setDocked(v > 40));

  return (
    <header className="fixed inset-x-0 top-0 z-50 flex justify-center px-3 pt-3">
      <motion.nav
        layout
        transition={{ duration: 0.6, ease: EASE }}
        className={cn(
          "flex w-full items-center justify-between rounded-full transition-[background,box-shadow,border-color] duration-500",
          docked
            ? "glass max-w-[880px] py-2 pl-5 pr-2 shadow-[var(--shadow-soft)]"
            : "max-w-[1200px] border border-transparent py-3 pl-4 pr-3",
        )}
      >
        <a href="#top" className="shrink-0">
          {brand}
        </a>
        <ul className="hidden items-center gap-1 md:flex">
          {links.map((l) => (
            <li key={l.href}>
              <a
                href={l.href}
                className="relative rounded-full px-3 py-2 font-display text-[14.5px] font-medium text-ink-2 transition-colors hover:text-ink after:absolute after:inset-x-3 after:bottom-1.5 after:h-px after:origin-left after:scale-x-0 after:bg-ink after:transition-transform after:duration-300 hover:after:scale-x-100"
              >
                {l.label}
              </a>
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-2">
          <Button href={cta.href} size="sm" className="hidden md:inline-flex">
            {cta.label}
          </Button>
          <button
            aria-label={open ? "Close menu" : "Open menu"}
            className="grid size-10 place-items-center rounded-full text-ink md:hidden"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X size={20} strokeWidth={1.6} /> : <Menu size={20} strokeWidth={1.6} />}
          </button>
        </div>
      </motion.nav>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -8, filter: "blur(8px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -8, filter: "blur(8px)" }}
            transition={{ duration: 0.35, ease: EASE }}
            className="glass absolute inset-x-3 top-[72px] rounded-[24px] p-3 shadow-[var(--shadow-lift)] md:hidden"
          >
            {links.map((l) => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="block rounded-2xl px-4 py-3 font-display text-[17px] font-medium text-ink hover:bg-[var(--hair)]"
              >
                {l.label}
              </a>
            ))}
            <Button href={cta.href} className="mt-2 w-full" size="lg">
              {cta.label}
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}

/** Pixel wordmark in the obliqq style, with a tiny suffix. */
export function Wordmark({ name, suffix }: { name: string; suffix?: string }) {
  return (
    <span className="inline-flex items-end gap-0.5 text-ink">
      <span className="font-pixel text-[22px] leading-none tracking-[0.02em]">{name}</span>
      {suffix && <span className="t-serif mb-[-2px] text-[13px] text-muted">{suffix}</span>}
    </span>
  );
}
