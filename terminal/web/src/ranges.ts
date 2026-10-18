export type Timeframe = '1m' | '2m' | '3m' | '5m' | '10m' | '15m' | '30m' | '1h' | '1D' | '1W' | '1M';

/** What a chart can show: a built-in `Timeframe` or an interval the person typed (`7m`, `2h`, `3D`; see interval.ts). The server is only ever asked for a `Timeframe`. */
export type Interval = string;

/** Whether bars of this interval are inside one trading day (the others are a day, a week or a month, stamped with a date). */
export const isIntraday = (tf: Interval): boolean => /^\d+[mh]$/.test(tf);

export interface Range {
  label: string;
  tf: Interval;
  /** Bars to show; null shows every bar; 'ytd' is computed from today's date. */
  bars: number | null | 'ytd';
  note?: string;
}

export const RANGES: Range[] = [
  { label: '1D', tf: '1m', bars: 375 },
  { label: '5D', tf: '5m', bars: 375 },
  { label: '1M', tf: '1D', bars: 22 },
  { label: '3M', tf: '1D', bars: 63 },
  { label: '6M', tf: '1D', bars: 126 },
  { label: 'YTD', tf: '1D', bars: 'ytd' },
  { label: '1Y', tf: '1D', bars: 252 },
  { label: '5Y', tf: '1D', bars: 1260 },
  { label: 'All', tf: '1D', bars: null },
];

/** How many daily bars a range shows on a given date. YTD is the trading days since 1 January, estimated at 252 a year. */
export function barLimit(range: Range, today: Date): number | null {
  if (range.bars === 'ytd') {
    const start = Date.UTC(today.getUTCFullYear(), 0, 1);
    const days = (Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()) - start) / 86_400_000 + 1;
    return Math.max(1, Math.ceil((days * 252) / 365));
  }
  return range.bars;
}
