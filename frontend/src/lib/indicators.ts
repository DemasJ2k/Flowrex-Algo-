/**
 * Client-side technical indicator calculations.
 * Used for chart overlays — computed from OHLCV candle data.
 */

export function ema(closes: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length < period) return result;

  const alpha = 2 / (period + 1);
  let sum = 0;
  for (let i = 0; i < period; i++) sum += closes[i];
  result[period - 1] = sum / period;

  for (let i = period; i < closes.length; i++) {
    result[i] = alpha * closes[i] + (1 - alpha) * (result[i - 1] as number);
  }
  return result;
}

export function sma(closes: number[], period: number): (number | null)[] {
  const result: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length < period) return result;

  let sum = 0;
  for (let i = 0; i < period; i++) sum += closes[i];
  result[period - 1] = sum / period;

  for (let i = period; i < closes.length; i++) {
    sum += closes[i] - closes[i - period];
    result[i] = sum / period;
  }
  return result;
}

export interface BollingerBand {
  upper: number | null;
  lower: number | null;
  middle: number | null;
}

export function bollingerBands(closes: number[], period: number = 20, stdDev: number = 2): BollingerBand[] {
  const result: BollingerBand[] = new Array(closes.length).fill(null).map(() => ({ upper: null, lower: null, middle: null }));
  const mid = sma(closes, period);

  for (let i = period - 1; i < closes.length; i++) {
    if (mid[i] === null) continue;
    const slice = closes.slice(i - period + 1, i + 1);
    const mean = mid[i] as number;
    const variance = slice.reduce((s, v) => s + (v - mean) ** 2, 0) / period;
    const sd = Math.sqrt(variance);
    result[i] = {
      upper: mean + stdDev * sd,
      lower: mean - stdDev * sd,
      middle: mean,
    };
  }
  return result;
}
