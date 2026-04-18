"use client";

import { motion } from "framer-motion";
import { useMemo } from "react";

interface StatRingProps {
  /** Value between 0 and 1 (e.g. 0.65 = 65%). Will be clamped. */
  value: number;
  /** Ring size in pixels. Default 120. */
  size?: number;
  /** Ring stroke width. Default 10. */
  stroke?: number;
  /** Primary (filled arc) color. Default uses accent gradient. */
  color?: string;
  /** Trailing (unfilled) color. */
  trackColor?: string;
  /** Label shown below the big number. */
  label?: string;
  /** Text to display in the ring center (overrides default %). */
  centerText?: string;
  /** Sub-label shown below center text. */
  subText?: string;
  /** Show the percentage sign. */
  showPercent?: boolean;
  className?: string;
}

/**
 * Circular gauge — fills based on `value` (0..1). Animates on mount.
 * Uses SVG strokeDasharray trick with framer-motion.
 */
export default function StatRing({
  value,
  size = 120,
  stroke = 10,
  color,
  trackColor = "rgba(139,92,246,0.12)",
  label,
  centerText,
  subText,
  showPercent = true,
  className = "",
}: StatRingProps) {
  const clamped = Math.max(0, Math.min(1, value || 0));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  const gradientId = useMemo(() => `ring-grad-${Math.random().toString(36).slice(2, 9)}`, []);

  return (
    <div className={`inline-flex flex-col items-center ${className}`}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <defs>
            <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%"  stopColor="#8b5cf6" />
              <stop offset="100%" stopColor="#3b82f6" />
            </linearGradient>
          </defs>
          {/* Track */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={trackColor}
            strokeWidth={stroke}
          />
          {/* Value arc */}
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color || `url(#${gradientId})`}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: circumference }}
            animate={{ strokeDashoffset: circumference * (1 - clamped) }}
            transition={{ duration: 1, ease: [0.32, 0.72, 0, 1] }}
          />
        </svg>
        {/* Center label */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="tabular text-2xl font-semibold" style={{ letterSpacing: "-0.02em" }}>
            {centerText !== undefined ? centerText : `${Math.round(clamped * 100)}${showPercent ? "%" : ""}`}
          </span>
          {subText && (
            <span className="text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>
              {subText}
            </span>
          )}
        </div>
      </div>
      {label && (
        <p className="mt-2 text-xs uppercase tracking-wider" style={{ color: "var(--muted)" }}>
          {label}
        </p>
      )}
    </div>
  );
}
