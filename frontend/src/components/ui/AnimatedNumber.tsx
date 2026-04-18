"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  value: number;
  duration?: number;
  format?: (v: number) => string;
  className?: string;
  /** Force-disable animation (for reduced-motion or SSR). */
  disabled?: boolean;
}

/**
 * Animates numeric values smoothly between renders.
 * Good for P&L, balance, win rate — gives the UI a "live" feel
 * without jarring jumps. Respects prefers-reduced-motion.
 */
export default function AnimatedNumber({
  value,
  duration = 700,
  format = (v) => v.toFixed(2),
  className = "tabular",
  disabled,
}: Props) {
  const [display, setDisplay] = useState(value);
  const startRef = useRef<number | null>(null);
  const fromRef = useRef(value);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    if (disabled || typeof window === "undefined") {
      setDisplay(value);
      return;
    }
    // Respect prefers-reduced-motion
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (mq?.matches) {
      setDisplay(value);
      return;
    }

    fromRef.current = display;
    startRef.current = null;

    const step = (ts: number) => {
      if (startRef.current === null) startRef.current = ts;
      const elapsed = ts - startRef.current;
      const t = Math.min(1, elapsed / duration);
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3);
      const next = fromRef.current + (value - fromRef.current) * eased;
      setDisplay(next);
      if (t < 1) frameRef.current = requestAnimationFrame(step);
    };

    frameRef.current = requestAnimationFrame(step);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, duration, disabled]);

  return <span className={className}>{format(display)}</span>;
}
