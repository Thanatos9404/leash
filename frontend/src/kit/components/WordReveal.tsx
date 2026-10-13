import { motion, useReducedMotion } from "motion/react";
import { EASE } from "../lib/motion";
import { cn } from "../lib/cn";

type Props = {
  /** Plain text. Wrap words in *asterisks* to set them in the italic serif accent. */
  text: string;
  as?: "h1" | "h2" | "p";
  className?: string;
  delay?: number;
  /** Seconds between words. */
  gap?: number;
};

/** The obliqq hero headline: each word sharpens out of a blur, left to right. */
export function WordReveal({ text, as = "h1", className, delay = 0.15, gap = 0.07 }: Props) {
  const reduce = useReducedMotion();
  const Tag = motion[as];
  const words = text.split(" ");
  return (
    <Tag className={cn(className)} aria-label={text.replace(/\*/g, "")}>
      {words.map((raw, i) => {
        const serif = raw.startsWith("*") && raw.endsWith("*");
        const word = raw.replace(/\*/g, "");
        return (
          <span key={i} aria-hidden className="inline-block whitespace-pre">
            <motion.span
              className={cn("inline-block", serif && "t-serif pr-[0.04em]")}
              initial={reduce ? { opacity: 0 } : { opacity: 0, filter: "blur(14px)", y: "0.18em" }}
              animate={reduce ? { opacity: 1 } : { opacity: 1, filter: "blur(0px)", y: "0em" }}
              transition={{ duration: 1, ease: EASE, delay: delay + i * gap }}
            >
              {word}
            </motion.span>
            {i < words.length - 1 ? " " : ""}
          </span>
        );
      })}
    </Tag>
  );
}
