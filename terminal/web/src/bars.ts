import { parseInterval, startOf } from './interval';
import { isIntraday, type Interval } from './ranges';

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
export function barTime(tsSec: number, tf: Interval): number {
  switch (tf) {
    case '2m':
    case '3m':
    case '10m':
    case '30m':
    case '1h': {
      // anchored to the 09:15 open, so a 30-minute bar starts 09:15, 09:45 and so on
      const width = { '2m': 120, '3m': 180, '10m': 600, '30m': 1800, '1h': 3600 }[tf];
      const local = tsSec + IST_OFFSET;
      const day = Math.floor(local / 86_400);
      const open = day * 86_400 + 9 * 3600 + 15 * 60;
      return open + Math.floor((local - open) / width) * width - IST_OFFSET;
    }
    case '1W': {
      const day = Math.floor((tsSec + IST_OFFSET) / 86_400);
      return (day - ((day + 3) % 7)) * 86_400; // Monday (1970-01-01 was a Thursday), as midnight UTC of the date
    }
    case '1M': {
      const d = new Date((Math.floor((tsSec + IST_OFFSET) / 86_400) * 86_400) * 1000);
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
    }
    case '1m':
      return Math.floor(tsSec / 60) * 60;
    case '5m':
      return Math.floor(tsSec / 300) * 300;
    case '15m':
      return Math.floor(tsSec / 900) * 900;
    case '1D':
      return Math.floor((tsSec + IST_OFFSET) / 86_400) * 86_400;
    default: {
      const p = parseInterval(tf); // an interval the person typed
      return p.ok ? startOf(tsSec, p) : Math.floor(tsSec / 60) * 60;
    }
  }
}

const OPEN_SEC = 9 * 3600 + 15 * 60; // 09:15 IST
const CLOSE_SEC = 15 * 3600 + 30 * 60; // 15:30 IST
const MAX_MOVE = 0.25; // no listed stock moves more than its 20% band in a day, so a tick 25% from the last bar is not this stock's price

/** Whether a moment falls inside the weekday trading session (09:15 to 15:30 IST). */
export function inSession(tsSec: number): boolean {
  const ist = tsSec + IST_OFFSET;
  const day = Math.floor(ist / 86_400);
  const weekday = (day + 4) % 7; // 1970-01-01 was a Thursday; 0 is Sunday
  if (weekday === 0 || weekday === 6) return false;
  const secs = ist - day * 86_400;
  return secs >= OPEN_SEC && secs < CLOSE_SEC;
}

/** Whether a tick should move the chart. Intraday bars only grow during the session (an exchange snapshot stamped after the close must not start a bar), and a price far from the last bar's is ignored rather than drawn as a candle that is not this stock's. */
export function tickFitsChart(last: Bar | undefined, price: number, tsSec: number, tf: Interval): boolean {
  if (!(price > 0)) return false;
  if (isIntraday(tf) && !inSession(tsSec)) return false;
  if (last && last.c > 0 && Math.abs(price / last.c - 1) > MAX_MOVE) return false;
  return true;
}

/** The bar to draw after a trade at `price`: the last bar updated, or a new bar when the trade falls in a later one. */
export function applyTick(last: Bar | undefined, price: number, tsSec: number, tf: Interval, volume = 0): Bar {
  const t = barTime(tsSec, tf);
  if (last && last.t === t) {
    return { ...last, h: Math.max(last.h, price), l: Math.min(last.l, price), c: price, v: last.v + volume };
  }
  if (last && t < last.t) return last; // a late tick for an earlier bar changes nothing
  return { t, o: price, h: price, l: price, c: price, v: volume };
}
