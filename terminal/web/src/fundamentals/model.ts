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

export interface DocLink {
  title: string;
  category: string;
  date: string;
  url: string;
  size: string;
}

export interface IndustryRow {
  label: string;
  company: number;
  industry: number;
  unit: string;
  /** `ratio`: compare = company / industry; `gap`: compare = company - industry, in percentage points. */
  relation: 'ratio' | 'gap';
  compare: number;
  how: string;
}

/** The company set against its Damodaran industry (see `athena.fundamentals.page`). */
export interface IndustryBlock {
  name: string;
  method: string | null;
  confidence: number | null;
  n_firms: number | null;
  data_as_of: string | null;
  flags: string[];
  rows: IndustryRow[];
  source: string;
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
  /** Absent when the reference tables are not loaded or the company has no industry in them. */
  industry?: IndustryBlock | null;
  /** Links to the exchange's own copies: annual reports, investor-call items and other announcements. */
  documents: { annual_reports: DocLink[]; concalls: DocLink[]; announcements: DocLink[] };
  /** Each holder group's share in percent by quarter, and the number of shareholders. */
  shareholding: { ends: string[]; rows: Row[] };
  coverage: { periods: number; first: string; last: string } | null;
  gaps: string[];
  /** Earlier symbols of this company whose periods are included, each with what changed. */
  history: { symbol: string; note: string; first: string; last: string }[];
}
