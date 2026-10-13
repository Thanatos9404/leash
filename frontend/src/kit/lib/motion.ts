import type { Transition, Variants } from "motion/react";

// One easing and one spring for the whole system, so every surface moves alike.
export const EASE = [0.22, 1, 0.36, 1] as const;
export const SPRING: Transition = { type: "spring", stiffness: 140, damping: 22, mass: 0.9 };
export const SPRING_SNAPPY: Transition = { type: "spring", stiffness: 420, damping: 32 };

// Blur-up entrance used by Reveal, WordReveal and Stagger children.
export const blurUp: Variants = {
  hidden: { opacity: 0, y: 24, filter: "blur(10px)" },
  show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.9, ease: EASE } },
};

export const stagger = (gap = 0.07, delay = 0): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: gap, delayChildren: delay } },
});
