import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import type { ReactNode } from "react";
import { blurUp, stagger } from "../lib/motion";

type RevealProps = HTMLMotionProps<"div"> & { delay?: number; children: ReactNode };

/** Blur-up on first scroll into view. The default entrance for every block. */
export function Reveal({ delay = 0, children, ...rest }: RevealProps) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : "hidden"}
      whileInView={reduce ? { opacity: 1 } : "show"}
      viewport={{ once: true, margin: "-12% 0px" }}
      variants={blurUp}
      transition={{ delay }}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

/** Staggers its direct <StaggerItem> children. */
export function Stagger({ gap = 0.07, delay = 0, children, ...rest }: RevealProps & { gap?: number }) {
  return (
    <motion.div
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: "-10% 0px" }}
      variants={stagger(gap, delay)}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, ...rest }: HTMLMotionProps<"div"> & { children: ReactNode }) {
  return (
    <motion.div variants={blurUp} {...rest}>
      {children}
    </motion.div>
  );
}
