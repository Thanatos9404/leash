import { motion, useScroll, useTransform, type MotionValue } from "motion/react";
import { useRef } from "react";
import { cn } from "../lib/cn";

/**
 * A large statement whose words light up as you scroll through it.
 * Use for the problem statement or a single honest claim, never fake quotes.
 */
export function ScrollWords({ text, className }: { text: string; className?: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 85%", "end 45%"] });
  const words = text.split(" ");
  return (
    <p ref={ref} className={cn("t-h2 max-w-[22ch] mx-auto text-center", className)}>
      {words.map((w, i) => (
        <Word key={i} progress={scrollYProgress} range={[i / words.length, (i + 1) / words.length]}>
          {w}
        </Word>
      ))}
    </p>
  );
}

function Word({ children, progress, range }: { children: string; progress: MotionValue<number>; range: [number, number] }) {
  const opacity = useTransform(progress, range, [0.16, 1]);
  const serif = children.startsWith("*") && children.endsWith("*");
  return (
    <motion.span style={{ opacity }} className={cn("inline-block mr-[0.24em]", serif && "t-serif")}>
      {children.replace(/\*/g, "")}
    </motion.span>
  );
}
