export type Cell = string | number | null;
export type Row = Record<string, Cell>;

/** A column's label and how it is shown. "pct" columns are stored as fractions and shown as percent. */
export type Style = 'text' | 'price' | 'pct' | 'number' | 'number0';
export const COLUMNS: Record<string, [string, Style]> = {
  symbol: ['Symbol', 'text'], name: ['Name', 'text'], kind: ['Type', 'text'], sector: ['Sector', 'text'],
  close: ['Close', 'price'], ret_1d: ['1D', 'pct'], ret_1m: ['1M', 'pct'], ret_3m: ['3M', 'pct'], ret_6m: ['6M', 'pct'], ret_1y: ['1Y', 'pct'],
  from_52w_high: ['From 52w high', 'pct'], from_52w_low: ['Above 52w low', 'pct'], rsi_14: ['RSI', 'number'], trend: ['Trend', 'text'],
  sma50_vs_200: ['50d vs 200d', 'pct'], market_cap_cr: ['Mkt cap (Rs cr)', 'number0'], traded_value_cr: ['Traded (Rs cr/day)', 'number'],
  pe: ['P/E', 'number'], pb: ['P/B', 'number'], roe: ['ROE', 'pct'], net_margin: ['Net margin', 'pct'], debt_to_equity: ['Debt/Equity', 'number'],
  dividend_yield: ['Dividend yield', 'pct'], earnings_yield: ['Earnings yield', 'pct'], revenue_growth: ['Sales growth (qtr, yoy)', 'pct'], flags: ['Flags', 'number0'],
};
export const DEFAULT_COLUMNS = ['symbol', 'name', 'close', 'ret_1m', 'ret_1y', 'from_52w_high', 'pe', 'pb', 'roe', 'market_cap_cr', 'trend', 'flags'];

/** The numeric filters: column -> [label, unit shown, factor from the shown unit to the stored one]. */
export const RANGE_FILTERS: Record<string, [string, string, number]> = {
  pe: ['P/E', 'x', 1], pb: ['P/B', 'x', 1], roe: ['ROE', '%', 0.01], ret_1y: ['1-year return', '%', 0.01], from_52w_high: ['From 52-week high', '%', 0.01],
  market_cap_cr: ['Market cap', 'Rs cr', 1], traded_value_cr: ['Traded per day', 'Rs cr', 1], dividend_yield: ['Dividend yield', '%', 0.01], debt_to_equity: ['Debt/Equity', 'x', 1],
};

export type Limits = Record<string, [number | null, number | null]>; // in stored units

export interface Preset {
  label: string;
  note: string;
  ranges: Limits;
  kinds: string[];
  trends: string[];
}

export const PRESETS: Preset[] = [
  { label: 'Value', note: 'Cheap against earnings and book, and earning well: P/E under 15, P/B under 3, ROE over 15%.', ranges: { pe: [0, 15], pb: [0, 3], roe: [0.15, null] }, kinds: ['stock'], trends: [] },
  { label: 'Quality compounders', note: 'ROE over 20%, low debt (Debt/Equity under 0.5), market cap over Rs 5,000 cr.', ranges: { roe: [0.2, null], debt_to_equity: [0, 0.5], market_cap_cr: [5000, null] }, kinds: ['stock'], trends: [] },
  { label: 'Momentum', note: 'Up over 20% in a year and within 10% of the 52-week high, with the trend aligned up.', ranges: { ret_1y: [0.2, null], from_52w_high: [-0.1, null] }, kinds: ['stock'], trends: ['aligned_up'] },
  { label: 'Beaten down', note: 'More than 40% below the 52-week high. A fall is not a reason to buy; look at why.', ranges: { from_52w_high: [null, -0.4], traded_value_cr: [1, null] }, kinds: ['stock'], trends: [] },
  { label: 'Dividend payers', note: 'Dividend yield over 3% on stocks that trade at least Rs 1 cr a day.', ranges: { dividend_yield: [0.03, null], traded_value_cr: [1, null] }, kinds: ['stock'], trends: [] },
  { label: 'Liquid large caps', note: 'Market cap over Rs 50,000 cr.', ranges: { market_cap_cr: [50000, null] }, kinds: ['stock'], trends: [] },
];

export interface Filters {
  kinds: string[]; // empty means every kind
  text: string;
  ranges: Limits;
  trends: string[];
  sector: string; // empty means every sector
  cleanOnly: boolean;
}

export const noFilters = (): Filters => ({ kinds: ['stock'], text: '', ranges: {}, trends: [], sector: '', cleanOnly: false });

/** Rows from the server's `{columns, rows}` form. */
export function toRows(columns: string[], rows: Cell[][]): Row[] {
  return rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i] ?? null])));
}

function within(value: Cell, low: number | null, high: number | null): boolean {
  if (low === null && high === null) return true;
  if (typeof value !== 'number') return false; // a missing figure is not a figure of zero, and does not meet a limit
  return (low === null || value >= low) && (high === null || value <= high);
}

/** The rows that pass every filter. A row with no figure in a column that has a limit does not pass. */
export function filterRows(rows: Row[], f: Filters): Row[] {
  const needle = f.text.trim().toLowerCase();
  const limits = Object.entries(f.ranges).filter(([, [lo, hi]]) => lo !== null || hi !== null);
  return rows.filter((row) => {
    if (f.kinds.length && !f.kinds.includes(String(row.kind))) return false;
    if (needle && !String(row.symbol).toLowerCase().includes(needle) && !String(row.name ?? '').toLowerCase().includes(needle)) return false;
    if (f.trends.length && !f.trends.includes(String(row.trend))) return false;
    if (f.sector && row.sector !== f.sector) return false;
    if (f.cleanOnly && row.flags) return false;
    return limits.every(([column, [lo, hi]]) => within(row[column], lo, hi));
  });
}

/** Sorted by a column, rows with no figure last whichever way it runs. */
export function sortRows(rows: Row[], column: string, descending: boolean): Row[] {
  const have = rows.filter((r) => r[column] !== null && r[column] !== undefined);
  const lacking = rows.filter((r) => r[column] === null || r[column] === undefined);
  have.sort((a, b) => {
    const x = a[column] as string | number;
    const y = b[column] as string | number;
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
    return descending ? -c : c;
  });
  return [...have, ...lacking];
}

/** The sentence at the top: how old the data is and what is missing. */
export function dataNote(rows: Row[], marketDay: string | null, today: Date): string {
  if (!marketDay) return 'No prices in the database yet. Run python -m athena.warehouse.build.';
  const stocks = rows.filter((r) => r.kind === 'stock');
  const withStatements = stocks.filter((r) => typeof r.pe === 'number' || typeof r.roe === 'number').length;
  const flagged = rows.filter((r) => r.flags).length;
  const age = Math.round((Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()) - Date.parse(marketDay)) / 86_400_000);
  const when = age <= 0 ? 'today' : `${age} day${age === 1 ? '' : 's'} old`;
  return `Prices to ${marketDay} (${when}). ${withStatements.toLocaleString('en-IN')} of ${stocks.length.toLocaleString('en-IN')} stocks have statements, so P/E, P/B and ROE are blank for the rest. ${flagged.toLocaleString('en-IN')} symbols carry a data flag.`;
}
