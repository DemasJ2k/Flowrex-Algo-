"use client";

import { useEffect, useRef } from "react";
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
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const disposedRef = useRef(false);

  useEffect(() => {
    if (!containerRef.current) return;
    disposedRef.current = false;

    // Clean up existing
    if (chartRef.current) {
      chartRef.current.remove();
    }

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

    const series = chart.addLineSeries({
      color: "#3b82f6",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
    });

    // Add area fill
    const areaSeries = chart.addAreaSeries({
      topColor: "rgba(59, 130, 246, 0.15)",
      bottomColor: "rgba(59, 130, 246, 0.02)",
      lineColor: "transparent",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Set data
    if (data.length > 0) {
      const lineData: LineData[] = data.map((d) => ({
        time: d.time as Time,
        value: d.value,
      }));
      series.setData(lineData);
      areaSeries.setData(lineData);
      chart.timeScale().fitContent();
    }

    // Resize handler
    const handleResize = () => {
      if (!disposedRef.current && chart && containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      disposedRef.current = true;
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [data, height]);

  return <div ref={containerRef} className="w-full" />;
}
