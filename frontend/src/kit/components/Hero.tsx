import { motion, useScroll, useTransform, useReducedMotion } from "motion/react";
import { useRef, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { EASE } from "../lib/motion";
import { cn } from "../lib/cn";
import { Button, Magnetic } from "./Button";
import { SkyBackdrop } from "./SkyBackdrop";
import { ShaderBackdrop } from "./ShaderBackdrop";
import { WordReveal } from "./WordReveal";

type HeroProps = {
  eyebrow?: ReactNode;
  /** Headline, max ~8 words. Wrap 1–2 words in *asterisks* for the serif accent. */
  title: string;
  subtitle: ReactNode;
  primary: { label: string; href: string };
  secondary?: { label: string; href: string };
  /** Live product UI or a real screenshot, rendered inside a tilting frame. */
  product?: ReactNode;
  /** "sky" = obliqq clouds, "shader" = living gradient, "none" = plain paper. */
  backdrop?: "sky" | "shader" | "none";
  shaderColors?: [string, string, string, string];
};

export function Hero({ eyebrow, title, subtitle, primary, secondary, product, backdrop = "sky", shaderColors }: HeroProps) {
  return (
    <section id="top" className="relative isolate overflow-hidden pt-36 md:pt-44">
      {backdrop === "sky" && <SkyBackdrop className="-z-10" />}
      {backdrop === "shader" && <ShaderBackdrop className="-z-10" colors={shaderColors} />}
      {backdrop !== "none" && (
        <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 h-40 bg-gradient-to-b from-transparent to-paper" />
      )}

      <div className="container-x flex flex-col items-center text-center">
        {eyebrow && (
          <motion.div
            initial={{ opacity: 0, y: 8, filter: "blur(6px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{ duration: 0.8, ease: EASE }}
            className="glass mb-7 inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 font-display text-[13px] font-medium text-ink-2"
          >
            {eyebrow}
          </motion.div>
        )}
        <WordReveal text={title} className="t-hero max-w-[16ch]" />
        <motion.div
          initial={{ opacity: 0, y: 12, filter: "blur(8px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          transition={{ duration: 1, ease: EASE, delay: 0.55 }}
          className="t-lead mt-6 max-w-[58ch]"
        >
          {subtitle}
        </motion.div>
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, ease: EASE, delay: 0.75 }}
          className="mt-9 flex flex-wrap items-center justify-center gap-3"
        >
          <Magnetic>
            <Button href={primary.href} size="lg" icon={<ArrowRight size={17} strokeWidth={1.8} />}>
              {primary.label}
            </Button>
          </Magnetic>
          {secondary && (
            <Button href={secondary.href} size="lg" variant="glass">
              {secondary.label}
            </Button>
          )}
        </motion.div>
      </div>

      {product && <TiltFrame className="mt-16 md:mt-20">{product}</TiltFrame>}
    </section>
  );
}

/**
 * The obliqq product reveal: a window that starts tipped back and straightens
 * as the user scrolls, rising out of the hero.
 */
export function TiltFrame({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "start 25%"] });
  const rotateX = useTransform(scrollYProgress, [0, 1], [reduce ? 0 : 24, 0]);
  const scale = useTransform(scrollYProgress, [0, 1], [reduce ? 1 : 0.9, 1]);
  const y = useTransform(scrollYProgress, [0, 1], [reduce ? 0 : 60, 0]);
  return (
    <div className={cn("container-x [perspective:1600px]", className)}>
      <motion.div
        ref={ref}
        initial={{ opacity: 0, y: 80 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 1.2, ease: EASE, delay: 0.9 }}
        style={{ rotateX, scale, y, transformOrigin: "50% 0%" }}
        className="relative mx-auto max-w-[1080px]"
      >
        <div className="rounded-[26px] border border-[var(--glass-border)] bg-[var(--glass)] p-2 shadow-[var(--shadow-lift)] backdrop-blur-md">
          <div className="overflow-hidden rounded-[20px] border border-hair bg-surface">{children}</div>
        </div>
      </motion.div>
    </div>
  );
}
