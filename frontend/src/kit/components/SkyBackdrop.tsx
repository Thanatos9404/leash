import { motion, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useId } from "react";
import { cn } from "../lib/cn";

/**
 * Morning-sky gradient with soft volumetric clouds drifting in from the edges.
 * Clouds are pure SVG (turbulence + displacement), so there are no image
 * licences to worry about.
 */
export function SkyBackdrop({ className, clouds = true }: { className?: string; clouds?: boolean }) {
  const { scrollY } = useScroll();
  const lift = useTransform(scrollY, [0, 600], [0, -80]);
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden sky", className)}>
      <div className="absolute inset-x-0 top-0 h-[60%] bg-[radial-gradient(60%_60%_at_50%_0%,rgba(255,255,255,0.35),transparent_70%)]" />
      {clouds && (
        <motion.div style={{ y: lift }} className="absolute inset-0">
          <Cloud className="left-[-140px] top-[16%] w-[420px]" drift={18} delay={0} />
          <Cloud className="right-[-160px] top-[12%] w-[460px]" drift={-16} delay={0.4} flip />
          <Cloud className="left-[8%] top-[64%] w-[260px] opacity-60" drift={10} delay={0.8} />
          <Cloud className="right-[6%] top-[58%] w-[300px] opacity-50" drift={-12} delay={1.2} flip />
        </motion.div>
      )}
      <Grain />
    </div>
  );
}

function Cloud({ className, drift, delay, flip }: { className?: string; drift: number; delay: number; flip?: boolean }) {
  const reduce = useReducedMotion();
  const id = useId().replace(/:/g, "");
  return (
    <motion.svg
      viewBox="0 0 400 200"
      className={cn("absolute", className)}
      style={{ scaleX: flip ? -1 : 1 }}
      initial={{ opacity: 0, x: drift * -3 }}
      animate={reduce ? { opacity: 1, x: 0 } : { opacity: 1, x: [0, drift, 0] }}
      transition={{
        opacity: { duration: 1.6, delay },
        x: { duration: 26, repeat: Infinity, ease: "easeInOut", delay },
      }}
    >
      <defs>
        <filter id={`f-${id}`} x="-30%" y="-30%" width="160%" height="160%">
          <feTurbulence type="fractalNoise" baseFrequency="0.012" numOctaves="4" seed={delay * 10 + 3} />
          <feDisplacementMap in="SourceGraphic" scale="58" />
          <feGaussianBlur stdDeviation="6" />
        </filter>
        <radialGradient id={`g-${id}`} cx="50%" cy="40%" r="60%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="70%" stopColor="#ffffff" stopOpacity="0.92" />
          <stop offset="100%" stopColor="#eef3fa" stopOpacity="0.6" />
        </radialGradient>
      </defs>
      <g filter={`url(#f-${id})`} fill={`url(#g-${id})`}>
        <ellipse cx="140" cy="120" rx="95" ry="52" />
        <ellipse cx="220" cy="96" rx="92" ry="62" />
        <ellipse cx="290" cy="126" rx="74" ry="44" />
        <ellipse cx="200" cy="140" rx="150" ry="38" />
      </g>
    </motion.svg>
  );
}

/** 3% film grain to kill gradient banding. Put it on any large gradient. */
export function Grain({ opacity = 0.06 }: { opacity?: number }) {
  return (
    <svg aria-hidden className="pointer-events-none absolute inset-0 h-full w-full mix-blend-soft-light" style={{ opacity }}>
      <filter id="kit-grain">
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
      <rect width="100%" height="100%" filter="url(#kit-grain)" />
    </svg>
  );
}
