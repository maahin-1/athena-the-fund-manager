/** The document `/api/fund/:symbol` returns (see `athena.fundamentals.page`). Rupee rows are already in crores; `null` is a figure the filings do not give. */
export interface Row {
  key: string;
  label: string;
  values: (number | null)[];
  ttm?: number | null;
  how?: string;
}

export interface KeyFigure {
  label: string;
  value: number;
  unit: string;
  how: string;
}

export interface GrowthTable {
  title: string;
  rows: { label: string; value: number | null }[];
}

export interface FundPage {
  symbol: string;
  name: string;
  layout: string;
  basis: 'consolidated' | 'standalone';
  bases: string[];
  as_of: string | null;
  price: number | null;
  price_note: string;
  key: KeyFigure[];
  quarters: { ends: string[]; rows: Row[] };
  annual: { ends: string[]; income: Row[]; balance: Row[]; cashflow: Row[]; banking: Row[] };
  ratios: { ends: string[]; rows: Row[] };
  growth: GrowthTable[];
  coverage: { periods: number; first: string; last: string } | null;
  gaps: string[];
}
