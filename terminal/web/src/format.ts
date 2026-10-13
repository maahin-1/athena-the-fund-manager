const PRICE = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 12,34,567.50 : Indian digit grouping, two decimals. */
export const price = (n: number): string => PRICE.format(n);

export const signed = (n: number): string => (n > 0 ? '+' : n < 0 ? '-' : '') + PRICE.format(Math.abs(n));

export const pct = (n: number): string => (n > 0 ? '+' : n < 0 ? '-' : '') + Math.abs(n).toFixed(2) + '%';

/** "2s", "3m", "1h" from milliseconds. */
export function age(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
}

/** Large quantities: 4.2M, 12.5K, 980. */
export function compact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e7) return (n / 1e7).toFixed(2) + 'Cr';
  if (a >= 1e5) return (n / 1e5).toFixed(2) + 'L';
  if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(Math.round(n));
}
