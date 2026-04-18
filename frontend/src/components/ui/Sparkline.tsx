"use client";

import { useMemo } from "react";

interface Props {
  data: number[];
  width?: number;
  height?: number;
  /** "up" = positive color, "down" = negative color, "auto" = based on first vs last value. */
  trend?: "up" | "down" | "auto";
  className?: string;
  /** Render a subtle gradient fill under the line. */
  fill?: boolean;
}

/**
 * Lightweight sparkline — SVG-only, no chart library.
 * Auto-determines trend color from first vs last value unless overridden.
 */
export default function Sparkline({
  data,
  width = 120,
  height = 32,
  trend = "auto",
  className = "",
  fill = true,
}: Props) {
  const { path, fillPath, colorUp } = useMemo(() => {
    if (!data || data.length < 2) {
      return { path: "", fillPath: "", colorUp: false };
    }
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const stepX = width / (data.length - 1);

    const points = data.map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return [x, y] as const;
    });

    const d = points
      .map(([x, y], i) => (i === 0 ? `M${x.toFixed(2)},${y.toFixed(2)}` : `L${x.toFixed(2)},${y.toFixed(2)}`))
      .join(" ");

    // Close to bottom for fill
    const fillD = d + ` L${(width).toFixed(2)},${height} L0,${height} Z`;

    const up = trend === "up" ? true : trend === "down" ? false : data[data.length - 1] >= data[0];
    return { path: d, fillPath: fillD, colorUp: up };
  }, [data, width, height, trend]);

  if (!data || data.length < 2) {
    return (
      <div
        className={`${className} opacity-30`}
        style={{ width, height, background: "var(--border)", borderRadius: 2 }}
      />
    );
  }

  const strokeColor = colorUp ? "var(--pnl-up)" : "var(--pnl-down)";
  const fillFrom = colorUp ? "rgba(16,185,129,0.25)" : "rgba(239,68,68,0.25)";
  const fillTo = "rgba(0,0,0,0)";
  const gradId = `sparkline-grad-${Math.random().toString(36).slice(2, 9)}`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      style={{ display: "block" }}
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%"   stopColor={fillFrom} />
              <stop offset="100%" stopColor={fillTo} />
            </linearGradient>
          </defs>
          <path d={fillPath} fill={`url(#${gradId})`} />
        </>
      )}
      <path
        d={path}
        fill="none"
        stroke={strokeColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
