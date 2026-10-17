import { fmtCrore, fmtPct, fmtPlain, growthClass, periodLabel } from './format';
import type { FundPage, GrowthTable, Row } from './model';

export interface Cell {
  text: string;
  cls: string;
}

export interface TableRow {
  key: string;
  label: string;
  cells: Cell[];
  how?: string;
}

export interface Table {
  head: string[];
  rows: TableRow[];
}

type Formatter = (value: number | null) => string;

const percent: Formatter = (v) => fmtPct(v, 1);
const ratio: Formatter = (v) => fmtPlain(v, 2);
const share: Formatter = (v) => fmtPct(v, 2);
const days: Formatter = (v) => fmtPlain(v, 0);
const FORMATS: Record<string, Formatter> = {
  opm: percent, tax_pct: percent, roe: percent, roce: percent, cfo_to_ebitda: percent, credit_deposit: percent,
  promoters: share, fii: share, dii: share, government: share, public: share, mutual_funds: share, insurance: share, banks: share, pension_funds: share, retail: share,
  employee_trusts: share, holders: (v) => fmtPlain(v, 0),
  cet1: percent, at1: percent, roa: percent, gross_npa: percent,
  eps: ratio, interest_cover: ratio, debt_to_equity: ratio,
  debtor_days: days, inventory_days: days, days_payable: days, cash_conversion_cycle: days, working_capital_days: days,
};

/** How a row's figure is written: rupee rows in crores, the listed ratios and per-share figures as their own kind. */
export function formatterFor(key: string): Formatter {
  return FORMATS[key] ?? fmtCrore;
}

function cell(key: string, value: number | null | undefined): Cell {
  const text = formatterFor(key)(value ?? null);
  return { text, cls: value !== null && value !== undefined && value < 0 ? 'dn' : '' };
}

/** A table of rows against period ends (oldest first). `ttm` adds the trailing-twelve-months column. */
export function tableFrom(ends: string[], rows: Row[], options: { ttm?: boolean } = {}): Table {
  const head = [...ends.map(periodLabel), ...(options.ttm ? ['TTM'] : [])];
  return {
    head,
    rows: rows.map((r) => ({
      key: r.key,
      label: r.label,
      cells: [...r.values.map((v) => cell(r.key, v)), ...(options.ttm ? [cell(r.key, r.ttm)] : [])],
      ...(r.how ? { how: r.how } : {}),
    })),
  };
}

export interface Block {
  label: string;
  text: string;
  how: string;
}

function keyText(value: number, unit: string): string {
  if (unit === 'Rs Cr') return `₹ ${fmtCrore(value)} Cr`;
  if (unit === 'Rs') return `₹ ${fmtPlain(value, 2)}`;
  if (unit === '%') return fmtPct(value, 1);
  return unit === 'x' ? fmtPlain(value, 2) : `${fmtPlain(value, 2)} ${unit}`;
}

/** The key-figure grid: market cap, price, P/E and the rest, each written for its unit and carrying its formula. */
export function keyBlocks(page: FundPage): Block[] {
  return page.key.map((k) => ({ label: k.label, how: k.how, text: keyText(k.value, k.unit) }));
}

export interface GrowthBlock {
  title: string;
  rows: { label: string; text: string; cls: string }[];
}

/** The compounded-growth boxes: each row a period and its percentage. */
export function growthBlocks(tables: GrowthTable[]): GrowthBlock[] {
  return tables.map((t) => ({ title: t.title, rows: t.rows.map((r) => ({ label: r.label, text: fmtPct(r.value, 0), cls: growthClass(r.value) })) }));
}
