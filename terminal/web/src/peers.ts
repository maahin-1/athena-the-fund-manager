import type { Cell, Row } from './screenlogic';

/** A stock's peers from the stored screen rows: the same industry when the exchange's classification is known and enough others share it, else the same
 *  sector, closest first in market cap. Pure: it reads rows it is handed and keeps no state. */

export const MIN_INDUSTRY_PEERS = 4;
export const DEFAULT_PEERS = 12;
export const MAX_PEERS = 30;

export type Basis = 'industry' | 'sector' | 'none';

export interface Peers {
  subject: Row;
  peers: Row[];
  basis: Basis;
  /** The industry or sector the peers share (empty when there are none). */
  group: string;
  /** Per numeric column, the median of the peers' figures (the subject not counted), or null when no peer has one. */
  medians: Record<string, number | null>;
}

const same = (a: Cell, b: Cell): boolean => typeof a === 'string' && typeof b === 'string' && a !== '' && a.toLowerCase() === b.toLowerCase();
const cap = (r: Row): number | null => (typeof r.market_cap_cr === 'number' && r.market_cap_cr > 0 ? r.market_cap_cr : null);

export function peersOf(rows: Row[], symbol: string, limit = DEFAULT_PEERS): Peers | null {
  const subject = rows.find((r) => String(r.symbol).toUpperCase() === symbol.toUpperCase());
  if (!subject || subject.kind !== 'stock') return null;
  const take = Math.min(MAX_PEERS, Math.max(1, Math.floor(limit)));
  const pool = rows.filter((r) => r !== subject && r.kind === 'stock' && cap(r) !== null);
  let basis: Basis = 'none';
  let chosen: Row[] = [];
  let group = '';
  const byIndustry = pool.filter((r) => same(r.industry, subject.industry));
  if (byIndustry.length >= MIN_INDUSTRY_PEERS) {
    basis = 'industry';
    chosen = byIndustry;
    group = String(subject.industry);
  } else {
    const bySector = pool.filter((r) => same(r.sector, subject.sector));
    if (bySector.length) {
      basis = 'sector';
      chosen = bySector;
      group = String(subject.sector);
    }
  }
  const mine = cap(subject);
  const distance = (r: Row): number => (mine === null ? -(cap(r) as number) : Math.abs(Math.log((cap(r) as number) / mine)));
  const peers = [...chosen].sort((a, b) => distance(a) - distance(b)).slice(0, take);
  const medians: Record<string, number | null> = {};
  for (const column of numericColumns(peers)) medians[column] = median(peers.map((r) => r[column]));
  return { subject, peers, basis, group, medians };
}

function numericColumns(rows: Row[]): string[] {
  const seen = new Set<string>();
  for (const r of rows) for (const [k, v] of Object.entries(r)) if (typeof v === 'number') seen.add(k);
  return [...seen];
}

export function median(values: Cell[]): number | null {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v)).sort((a, b) => a - b);
  if (!nums.length) return null;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}

/** The figures worth comparing and which end of each is the better one. A loss-making P/E is not comparable and is left out of ranks. */
export const COMPARE: { column: string; better: 'high' | 'low' | 'none' }[] = [
  { column: 'market_cap_cr', better: 'none' }, { column: 'pe', better: 'low' }, { column: 'pb', better: 'low' }, { column: 'roe', better: 'high' },
  { column: 'net_margin', better: 'high' }, { column: 'debt_to_equity', better: 'low' }, { column: 'dividend_yield', better: 'high' },
  { column: 'revenue_growth', better: 'high' }, { column: 'ret_1y', better: 'high' }, { column: 'from_52w_high', better: 'high' },
];

export interface Standing {
  column: string;
  better: 'high' | 'low' | 'none';
  value: number | null;
  median: number | null;
  /** 1 is the best among the subject and the peers that have a figure; null for a figure with no better end or none held. */
  rank: number | null;
  /** How many peers have a figure (the subject is one more). */
  of: number;
  /** How many of those peers the subject beats. */
  beats: number | null;
  /** The share of those peers the subject beats, 0 to 1. */
  percentile: number | null;
}

export function standing(p: Peers, columns: string[] = COMPARE.map((c) => c.column)): Standing[] {
  return columns.map((column) => {
    const better = COMPARE.find((c) => c.column === column)?.better ?? 'none';
    const usable = (v: Cell): v is number => typeof v === 'number' && Number.isFinite(v) && !(column === 'pe' && v <= 0);
    const value = usable(p.subject[column]) ? (p.subject[column] as number) : null;
    const theirs = p.peers.map((r) => r[column]).filter(usable);
    const base = { column, better, value, median: p.medians[column] ?? null, of: theirs.length + 1 };
    if (value === null || better === 'none' || !theirs.length) return { ...base, rank: null, beats: null, percentile: null };
    const above = theirs.filter((v) => (better === 'high' ? v > value : v < value)).length;
    const beats = theirs.filter((v) => (better === 'high' ? v < value : v > value)).length;
    return { ...base, rank: above + 1, beats, percentile: beats / theirs.length };
  });
}

/** The indices a stock is in, from the stored `indices` text. */
export function indicesOf(row: Row): string[] {
  return typeof row.indices === 'string' && row.indices ? row.indices.split('|') : [];
}
