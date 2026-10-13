import type { Timeframe } from './ranges';

export interface Bar {
  t: number; // unix seconds; a daily bar is midnight UTC of its trading date
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

const IST_OFFSET = 19_800; // seconds

/** Start of the bar a timestamp falls in. Intraday bars are aligned to the clock (IST is a whole number of 15 minutes from UTC). */
export function barTime(tsSec: number, tf: Timeframe): number {
  switch (tf) {
    case '1m':
      return Math.floor(tsSec / 60) * 60;
    case '5m':
      return Math.floor(tsSec / 300) * 300;
    case '15m':
      return Math.floor(tsSec / 900) * 900;
    case '1D':
      return Math.floor((tsSec + IST_OFFSET) / 86_400) * 86_400;
  }
}

/** The bar to draw after a trade at `price`: the last bar updated, or a new bar when the trade falls in a later one. */
export function applyTick(last: Bar | undefined, price: number, tsSec: number, tf: Timeframe, volume = 0): Bar {
  const t = barTime(tsSec, tf);
  if (last && last.t === t) {
    return { ...last, h: Math.max(last.h, price), l: Math.min(last.l, price), c: price, v: last.v + volume };
  }
  if (last && t < last.t) return last; // a late tick for an earlier bar changes nothing
  return { t, o: price, h: price, l: price, c: price, v: volume };
}
