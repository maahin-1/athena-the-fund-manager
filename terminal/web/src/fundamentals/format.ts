const WHOLE = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const fixed = (digits: number): Intl.NumberFormat => new Intl.NumberFormat('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const FIXED = [fixed(0), fixed(1), fixed(2)];

const missing = (value: number | null | undefined): value is null | undefined => value === null || value === undefined || !Number.isFinite(value);

/** A rupee figure in crores with Indian grouping: 12,34,568 from a hundred crore up, two decimals below. A missing figure is `-`. */
export function fmtCrore(value: number | null | undefined): string {
  if (missing(value)) return '-';
  return Math.abs(value) >= 100 ? WHOLE.format(value) : FIXED[2].format(value);
}

/** A plain figure to 0, 1 or 2 decimals with Indian grouping. A missing figure is `-`. */
export function fmtPlain(value: number | null | undefined, digits: 0 | 1 | 2 = 2): string {
  return missing(value) ? '-' : FIXED[digits].format(value);
}

/** `45.9%`; a missing figure is `-`. */
export function fmtPct(value: number | null | undefined, digits: 0 | 1 | 2 = 1): string {
  return missing(value) ? '-' : `${FIXED[digits].format(value)}%`;
}

/** `2024-03-31` becomes `Mar 2024`. */
export function periodLabel(iso: string): string {
  const [year, month] = iso.split('-');
  const name = MONTHS[Number(month) - 1];
  return name ? `${name} ${year}` : iso;
}

/** The colour class of a change: the page's own `up`, `dn` and `flat`. */
export function growthClass(value: number | null | undefined): 'up' | 'dn' | 'flat' {
  if (missing(value) || value === 0) return 'flat';
  return value > 0 ? 'up' : 'dn';
}
