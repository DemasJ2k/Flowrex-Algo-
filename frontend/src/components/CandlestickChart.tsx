"use client";

import { useEffect, useRef, useCallback } from "react";
import { createChart, IChartApi, ISeriesApi, CandlestickData, HistogramData, LineData, Time, SeriesMarker } from "lightweight-charts";
import type { CandleData } from "@/types";
import { ema, sma, bollingerBands } from "@/lib/indicators";

export interface ChartIndicators {
  ema8?: boolean;
  ema21?: boolean;
  ema50?: boolean;
  sma200?: boolean;
  bollinger?: boolean;
}

export interface ChartMarker {
  time: number;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle";
  text: string;
}

export default function CandlestickChart({
  candles,
  width,
  height = 400,
  indicators = {},
  markers = [],
}: {
  candles: CandleData[];
  width?: number;
  height?: number;
  indicators?: ChartIndicators;
  markers?: ChartMarker[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const indicatorSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const disposedRef = useRef(false);

  const initChart = useCallback(() => {
    if (!containerRef.current) return;

    if (chartRef.current) {
      chartRef.current.remove();
    }
    indicatorSeriesRef.current = [];

    const chart = createChart(containerRef.current, {
      width: width || containerRef.current.clientWidth,
      height,
      layout: {
        background: { color: "#0a0b0f" },
        textColor: "#71717a",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e2028" },
        horzLines: { color: "#1e2028" },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: "#3b82f6", width: 1, style: 2, labelBackgroundColor: "#3b82f6" },
        horzLine: { color: "#3b82f6", width: 1, style: 2, labelBackgroundColor: "#3b82f6" },
      },
      rightPriceScale: {
        borderColor: "#1e2028",
        scaleMargins: { top: 0.05, bottom: 0.2 },
      },
      timeScale: {
        borderColor: "#1e2028",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    const volumeSeries = chart.addHistogramSeries({
      color: "#3b82f680",
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
  }, [height, width]);

  // Initialize chart
  useEffect(() => {
    disposedRef.current = false;
    initChart();
    return () => {
      disposedRef.current = true;
      chartRef.current?.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      indicatorSeriesRef.current = [];
    };
  }, [initChart]);

  // Update data + indicators + markers
  useEffect(() => {
    if (disposedRef.current || !candleSeriesRef.current || !volumeSeriesRef.current || !chartRef.current || candles.length === 0) return;

    const chart = chartRef.current;
    const closes = candles.map((c) => c.close);
    const times = candles.map((c) => c.time);

    // Set candlestick data
    const candleData: CandlestickData[] = candles.map((c) => ({
      time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close,
    }));
    const volumeData: HistogramData[] = candles.map((c) => ({
      time: c.time as Time, value: c.volume, color: c.close >= c.open ? "#22c55e40" : "#ef444440",
    }));
    candleSeriesRef.current.setData(candleData);
    volumeSeriesRef.current.setData(volumeData);

    // Remove old indicator series
    for (const s of indicatorSeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* ignore */ }
    }
    indicatorSeriesRef.current = [];

    // Helper to add a line indicator
    const addLine = (values: (number | null)[], color: string, lineWidth: number = 1, dash?: boolean) => {
      const series = chart.addLineSeries({
        color,
        lineWidth: lineWidth as 1 | 2 | 3 | 4,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        ...(dash ? { lineStyle: 2 } : {}),
      });
      const data: LineData[] = [];
      for (let i = 0; i < values.length; i++) {
        if (values[i] !== null) {
          data.push({ time: times[i] as Time, value: values[i] as number });
        }
      }
      series.setData(data);
      indicatorSeriesRef.current.push(series);
    };

    // EMA overlays
    if (indicators.ema8) addLine(ema(closes, 8), "#facc15", 1);      // yellow
    if (indicators.ema21) addLine(ema(closes, 21), "#f97316", 1);     // orange
    if (indicators.ema50) addLine(ema(closes, 50), "#a855f7", 1);     // purple
    if (indicators.sma200) addLine(sma(closes, 200), "#ffffff80", 1, true); // white dotted

    // Bollinger Bands
    if (indicators.bollinger) {
      const bb = bollingerBands(closes, 20, 2);
      const upper = bb.map((b) => b.upper);
      const lower = bb.map((b) => b.lower);
      addLine(upper, "#6b728060", 1);
      addLine(lower, "#6b728060", 1);
    }

    // Trade markers
    if (markers.length > 0 && candleSeriesRef.current) {
      const chartMarkers: SeriesMarker<Time>[] = markers
        .filter((m) => m.time >= times[0] && m.time <= times[times.length - 1])
        .sort((a, b) => a.time - b.time)
        .map((m) => ({
          time: m.time as Time,
          position: m.position,
          color: m.color,
          shape: m.shape,
          text: m.text,
        }));
      candleSeriesRef.current.setMarkers(chartMarkers);
    }

    chart.timeScale().fitContent();
  }, [candles, indicators, markers]);

  // Resize
  useEffect(() => {
    const handleResize = () => {
      if (!disposedRef.current && chartRef.current && containerRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  return <div ref={containerRef} className="w-full" />;
}
