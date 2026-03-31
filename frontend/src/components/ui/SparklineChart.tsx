"use client";

/**
 * Tiny inline SVG sparkline chart — no library dependency.
 * Shows a mini line chart for stat cards and agent performance cards.
 */
export default function SparklineChart({
  data,
  width = 80,
  height = 32,
  color,
  strokeWidth = 1.5,
}: {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  strokeWidth?: number;
}) {
  if (!data || data.length < 2) {
    return <div style={{ width, height }} />;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const padding = 2;

  // Normalize to SVG coordinates (y-axis inverted)
  const points = data.map((val, i) => {
    const x = padding + (i / (data.length - 1)) * (width - padding * 2);
    const y = padding + (1 - (val - min) / range) * (height - padding * 2);
    return `${x},${y}`;
  });

  // Auto-detect trend color if not provided
  const lineColor = color || (data[data.length - 1] >= data[0] ? "#22c55e" : "#ef4444");

  // Create area fill path (line + close to bottom)
  const areaPath = `M${points[0]} ${points.slice(1).map((p) => `L${p}`).join(" ")} L${width - padding},${height - padding} L${padding},${height - padding} Z`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="flex-shrink-0">
      {/* Area fill */}
      <path d={areaPath} fill={lineColor} fillOpacity={0.1} />
      {/* Line */}
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={lineColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* End dot */}
      <circle
        cx={parseFloat(points[points.length - 1].split(",")[0])}
        cy={parseFloat(points[points.length - 1].split(",")[1])}
        r={2}
        fill={lineColor}
      />
    </svg>
  );
}
