/** The IPO tab's document (see `athena.ipo.page`): read from the server, never trusted for its shape. Pure. */
export interface FilingRow {
  company: string;
  kind: string;
  filed: string;
  title: string;
  page: string;
  pdf: string;
}

export interface SubRow {
  category: string;
  offered: number | null;
  bid: number | null;
  times: number | null;
  level: number;
}

export interface Subscription {
  takenAt: string;
  snapshots: number;
  totalTimes: number | null;
  rows: SubRow[];
}

export interface IssueRow {
  symbol: string;
  name: string;
  kind: string;
  status: string;
  start: string;
  end: string;
  priceLow: number | null;
  priceHigh: number | null;
  shares: number | null;
  lot: number | null;
  /** The most the issue can raise, in crore rupees: shares times the upper price. An estimate. */
  sizeCr: number | null;
  minApplication: number | null;
  subscription: Subscription | null;
  filings: FilingRow[];
}

export interface ListedRow {
  symbol: string;
  name: string;
  kind: string;
  issuePrice: number | null;
  listing: string;
  listingOpen: number | null;
  listingPct: number | null;
  firstClosePct: number | null;
  lastClose: number | null;
  lastPct: number | null;
  filings: FilingRow[];
}

export interface SourceRow {
  id: string;
  name: string;
  status: string;
  reason: string;
  lastOk: string;
}

export interface IpoDoc {
  asOf: string;
  open: IssueRow[];
  upcoming: IssueRow[];
  closed: IssueRow[];
  listed: ListedRow[];
  filed: FilingRow[];
  sources: SourceRow[];
  caveat: string;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isObject) : []);
/** A link the page may open: only https. */
export const safeLink = (v: unknown): string => (typeof v === 'string' && /^https:\/\//i.test(v) ? v : '');

const filing = (r: Record<string, unknown>): FilingRow => ({ company: str(r.company), kind: str(r.kind) || 'Other', filed: str(r.filed), title: str(r.title), page: safeLink(r.page), pdf: safeLink(r.pdf) });

const issue = (r: Record<string, unknown>): IssueRow => {
  const s = isObject(r.subscription) ? r.subscription : null;
  return {
    symbol: str(r.symbol), name: str(r.name), kind: str(r.kind) === 'sme' ? 'sme' : 'mainboard', status: str(r.status), start: str(r.start), end: str(r.end), priceLow: numOrNull(r.price_low), priceHigh: numOrNull(r.price_high),
    shares: numOrNull(r.shares), lot: numOrNull(r.lot), sizeCr: numOrNull(r.size_cr), minApplication: numOrNull(r.min_application),
    subscription: s
      ? { takenAt: str(s.taken_at), snapshots: numOrNull(s.snapshots) ?? 0, totalTimes: numOrNull(s.total_times), rows: list(s.rows).map((x): SubRow => ({ category: str(x.category), offered: numOrNull(x.offered), bid: numOrNull(x.bid), times: numOrNull(x.times), level: x.level === 1 ? 1 : 0 })) }
      : null,
    filings: list(r.filings).map(filing),
  };
};

export function readIpo(raw: unknown): IpoDoc | null {
  if (!isObject(raw) || typeof raw.as_of !== 'string' || !Array.isArray(raw.open) || !Array.isArray(raw.listed)) return null;
  return {
    asOf: raw.as_of, open: list(raw.open).map(issue), upcoming: list(raw.upcoming).map(issue), closed: list(raw.closed).map(issue),
    listed: list(raw.listed).map((r): ListedRow => ({
      symbol: str(r.symbol), name: str(r.name), kind: str(r.kind) === 'sme' ? 'sme' : 'mainboard', issuePrice: numOrNull(r.issue_price), listing: str(r.listing), listingOpen: numOrNull(r.listing_open), listingPct: numOrNull(r.listing_pct),
      firstClosePct: numOrNull(r.first_close_pct), lastClose: numOrNull(r.last_close), lastPct: numOrNull(r.last_pct), filings: list(r.filings).map(filing),
    })),
    filed: list(raw.filed).map(filing),
    sources: list(raw.sources).map((r): SourceRow => ({ id: str(r.id), name: str(r.name), status: str(r.status) || 'never tried', reason: str(r.reason), lastOk: str(r.last_ok) })),
    caveat: str(raw.caveat),
  };
}

/** The price band as a reader writes it; a missing band is a dash, never zero. */
export function bandText(low: number | null, high: number | null): string {
  if (high === null) return '-';
  return low === null || low === high ? `Rs ${high}` : `Rs ${low} to ${high}`;
}

/** A subscription multiple (2.5x); a missing one is a dash. */
export const timesText = (t: number | null): string => (t === null ? '-' : `${t < 10 ? t.toFixed(2) : t.toFixed(1)}x`);

/** A signed percentage for a gain against the issue price; missing is a dash. */
export const gainText = (p: number | null): string => (p === null ? '-' : `${p > 0 ? '+' : ''}${p.toFixed(1)}%`);
export const gainClass = (p: number | null): 'up' | 'dn' | 'flat' => (p === null || p === 0 ? 'flat' : p > 0 ? 'up' : 'dn');

/** The size line: crore rupees with the note that it is the most the issue can raise. */
export const sizeText = (cr: number | null): string => (cr === null ? '-' : `up to Rs ${cr.toLocaleString('en-IN')} cr`);

/** A source's status in the chips: ok is plain, anything else is flagged. */
export const sourceClass = (status: string): 'up' | 'dn' | 'flat' => (status === 'ok' ? 'up' : status === 'never tried' ? 'flat' : 'dn');

/** What to tell the person when the server refuses or fails. */
export function ipoProblem(status: number): string {
  return status === 429 ? 'The IPO page is already being built; try again in a moment.' : 'The IPO page could not be built just now; try again in a minute.';
}
