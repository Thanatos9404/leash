import { motion, useMotionValue, useSpring } from "motion/react";
import { forwardRef, useRef, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { cn } from "../lib/cn";
import { SPRING_SNAPPY } from "../lib/motion";

type Variant = "primary" | "glass" | "ghost" | "accent";
type Size = "sm" | "md" | "lg";

const base =
  "group relative inline-flex select-none items-center justify-center gap-2 overflow-hidden rounded-full font-display font-semibold tracking-[-0.01em] transition-[background,color,box-shadow,transform] duration-300 ease-[var(--ease-out-soft)] active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50";

const variants: Record<Variant, string> = {
  primary:
    "bg-ink text-paper shadow-[inset_0_1px_0_rgba(255,255,255,0.14),0_6px_20px_-8px_rgba(26,22,21,0.55)] hover:-translate-y-px hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.18),0_14px_30px_-10px_rgba(26,22,21,0.6)]",
  accent:
    "bg-accent text-[var(--accent-ink)] shadow-[inset_0_1px_0_rgba(255,255,255,0.22),0_8px_24px_-10px_var(--accent)] hover:-translate-y-px",
  glass: "glass text-ink hover:bg-[color-mix(in_oklab,var(--glass)_60%,white)] hover:-translate-y-px",
  ghost: "text-ink hover:bg-[var(--hair)]",
};

const sizes: Record<Size, string> = {
  sm: "h-9 px-4 text-[13.5px]",
  md: "h-11 px-5 text-[15px]",
  lg: "h-[52px] px-7 text-[16px]",
};

type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  variant?: Variant;
  size?: Size;
  href?: string;
  icon?: ReactNode;
};

/** Pill button with a light sweep on hover. Renders <a> when given href. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", href, icon, className, children, ...rest },
  ref,
) {
  const cls = cn(base, variants[variant], sizes[size], className);
  const inner = (
    <>
      <span className="pointer-events-none absolute inset-y-0 -left-1/2 w-1/3 -skew-x-12 bg-gradient-to-r from-transparent via-white/25 to-transparent opacity-0 transition-all duration-700 group-hover:left-[120%] group-hover:opacity-100" />
      <span className="relative">{children}</span>
      {icon && <span className="relative transition-transform duration-300 group-hover:translate-x-0.5">{icon}</span>}
    </>
  );
  if (href) {
    return (
      <a href={href} className={cls}>
        {inner}
      </a>
    );
  }
  return (
    <button ref={ref} className={cls} {...rest}>
      {inner}
    </button>
  );
});

/** Wrap any element so it leans toward the cursor. Use on the hero CTA only. */
export function Magnetic({ children, strength = 0.28 }: { children: ReactNode; strength?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const x = useSpring(useMotionValue(0), SPRING_SNAPPY as never);
  const y = useSpring(useMotionValue(0), SPRING_SNAPPY as never);
  return (
    <motion.div
      ref={ref}
      style={{ x, y }}
      className="inline-block"
      onPointerMove={(e) => {
        const r = ref.current!.getBoundingClientRect();
        x.set((e.clientX - (r.left + r.width / 2)) * strength);
        y.set((e.clientY - (r.top + r.height / 2)) * strength);
      }}
      onPointerLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      {children}
    </motion.div>
  );
}
