"use client";

import { useEffect, useRef, useCallback } from "react";
import { createChart, IChartApi, ISeriesApi, LineData, Time } from "lightweight-charts";

export default function EquityCurveChart({
  data,
  height = 200,
}: {
  data: { time: number; value: number }[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const areaRef = useRef<ISeriesApi<"Area"> | null>(null);

  // Create chart ONCE on mount
  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height,
      layout: {
        background: { color: "transparent" },
        textColor: "#71717a",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e202820" },
        horzLines: { color: "#1e202840" },
      },
      rightPriceScale: {
        borderColor: "#1e2028",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: "#1e2028",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        vertLine: { color: "#3b82f640", width: 1, style: 2 },
        horzLine: { color: "#3b82f640", width: 1, style: 2 },
      },
    });

    lineRef.current = chart.addLineSeries({
      color: "#3b82f6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
    });

    areaRef.current = chart.addAreaSeries({
      topColor: "rgba(59, 130, 246, 0.15)",
      bottomColor: "rgba(59, 130, 246, 0.02)",
      lineColor: "transparent",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;

    const handleResize = () => {
      if (chartRef.current && containerRef.current) {
        try { chartRef.current.applyOptions({ width: containerRef.current.clientWidth }); } catch {}
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chartRef.current = null;
      lineRef.current = null;
      areaRef.current = null;
      try { chart.remove(); } catch {}
    };
  }, [height]);

  // Update data WITHOUT recreating chart
  useEffect(() => {
    if (!lineRef.current || !areaRef.current || !chartRef.current) return;
    if (data.length === 0) return;

    const lineData: LineData[] = data.map((d) => ({
      time: d.time as Time,
      value: d.value,
    }));

    try {
      lineRef.current.setData(lineData);
      areaRef.current.setData(lineData);
      chartRef.current.timeScale().fitContent();
    } catch {}
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
