export type Timeframe = '1m' | '5m' | '15m' | '1D';

export interface Range {
  label: string;
  tf: Timeframe;
  /** Bars to show; null shows every bar; 'ytd' is computed from today's date. */
  bars: number | null | 'ytd';
  note?: string;
}

export const RANGES: Range[] = [
  { label: '1D', tf: '1m', bars: 375 },
  { label: '5D', tf: '5m', bars: 375 },
  { label: '1M', tf: '15m', bars: null, note: 'Intraday history is limited to 5 days.' },
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
