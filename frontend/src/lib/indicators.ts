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

export function rsi(closes: number[], period: number = 14): (number | null)[] {
  const result: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return result;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) avgGain += diff;
    else avgLoss += Math.abs(diff);
  }
  avgGain /= period;
  avgLoss /= period;

  result[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? Math.abs(diff) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    result[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return result;
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
