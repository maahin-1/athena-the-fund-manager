import type { Bar } from '../bars';
import type { Timeframe } from '../ranges';

const IST = 19_800;
const OPEN = 9 * 3600 + 15 * 60; // 09:15 IST, in seconds after midnight
const DAY = 86_400;

/** Minutes per bar of the intraday intervals the Chart Lab offers. */
export const MINUTES: Partial<Record<Timeframe, number>> = { '1m': 1, '2m': 2, '3m': 3, '5m': 5, '10m': 10, '15m': 15, '30m': 30, '1h': 60 };

/** The interval the server is asked for, and the factor to combine its bars by, for each interval. Intervals the exchange-side source serves
 *  itself have factor 1; the others are built from a finer one. */
export const SOURCE: Record<Timeframe, { fetch: Timeframe; factor: number }> = {
  '1m': { fetch: '1m', factor: 1 }, '2m': { fetch: '1m', factor: 2 }, '3m': { fetch: '1m', factor: 3 }, '5m': { fetch: '5m', factor: 1 },
  '10m': { fetch: '5m', factor: 2 }, '15m': { fetch: '15m', factor: 1 }, '30m': { fetch: '30m', factor: 1 }, '1h': { fetch: '1h', factor: 1 },
  '1D': { fetch: '1D', factor: 1 }, '1W': { fetch: '1D', factor: 1 }, '1M': { fetch: '1D', factor: 1 },
};

function merge(group: Bar[], t: number): Bar {
  return {
    t, o: group[0].o, h: Math.max(...group.map((b) => b.h)), l: Math.min(...group.map((b) => b.l)), c: group[group.length - 1].c,
    v: group.reduce((sum, b) => sum + b.v, 0),
  };
}

/** Combine intraday bars into bars of `minutes` minutes, aligned to the 09:15 IST open (so 30-minute bars start 09:15, 09:45, and so on). A bar
 *  that spans fewer source bars at the close is kept as it is. */
export function resampleIntraday(bars: Bar[], minutes: number): Bar[] {
  const out: Bar[] = [];
  let group: Bar[] = [];
  let bucket = -1;
  let start = 0;
  const width = minutes * 60;
  for (const b of bars) {
    const local = b.t + IST;
    const day = Math.floor(local / DAY);
    const slot = day * 1_000_000 + Math.floor((local - day * DAY - OPEN) / width);
    if (slot !== bucket && group.length) {
      out.push(merge(group, start));
      group = [];
    }
    if (!group.length) start = day * DAY + OPEN + Math.floor((local - day * DAY - OPEN) / width) * width - IST;
    bucket = slot;
    group.push(b);
  }
  if (group.length) out.push(merge(group, start));
  return out;
}

/** The Monday (week) or the first day (month) a daily bar belongs to, as the midnight-UTC stamp daily bars use. */
function periodStart(t: number, unit: 'W' | 'M'): number {
  const date = new Date(t * 1000);
  if (unit === 'M') return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1) / 1000;
  const monday = (date.getUTCDay() + 6) % 7; // Monday is 0
  return t - monday * DAY;
}

/** Combine daily bars into weekly (Monday to Friday) or monthly bars, stamped with the first day of the period. */
export function resampleDaily(bars: Bar[], unit: 'W' | 'M'): Bar[] {
  const out: Bar[] = [];
  let group: Bar[] = [];
  let start = -1;
  for (const b of bars) {
    const s = periodStart(b.t, unit);
    if (s !== start && group.length) {
      out.push(merge(group, start));
      group = [];
    }
    start = s;
    group.push(b);
  }
  if (group.length) out.push(merge(group, start));
  return out;
}

/** The bars for `tf` from what was fetched for `SOURCE[tf].fetch`. */
export function build(tf: Timeframe, fetched: Bar[]): Bar[] {
  if (tf === '1W') return resampleDaily(fetched, 'W');
  if (tf === '1M') return resampleDaily(fetched, 'M');
  const { factor } = SOURCE[tf];
  const minutes = MINUTES[tf];
  return factor > 1 && minutes ? resampleIntraday(fetched, minutes) : fetched;
}
