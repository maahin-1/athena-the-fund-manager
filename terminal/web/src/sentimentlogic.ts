/** The Sentiment page's document (see `athena.sentiment.page`): read from the server, never trusted for its shape. Pure. */
export interface NewsRow {
  date: string;
  /** When it was published and when we first saw it (ISO), the citation's times; empty for a row the older path found. */
  published: string;
  firstSeen: string;
  publisher: string;
  source: string;
  /** Which source gave it (a registry id), and its starting trust tier: A the exchange or a regulator, B an established publisher, C anything else. */
  sourceId: string;
  tier: string;
  title: string;
  link: string;
  routine: boolean;
  used: boolean;
}

/** A regulator or policy item (SEBI, RBI): about the market, not matched to a company. */
export interface MarketRow {
  date: string;
  publisher: string;
  sourceId: string;
  tier: string;
  title: string;
  link: string;
}

/** One source and how it is doing. `status` is ok, stale, empty, failed, off or never tried; `reason` says why it is not ok. */
export interface SourceRow {
  id: string;
  name: string;
  kind: string;
  tier: string;
  status: string;
  reason: string;
  held: number;
  newest: string;
  rowsHere: number;
}

/** One insider filing: who, what they did, how many shares and for how much (rupees), and when the exchange stamped it. */
export interface InsiderRow {
  filed: string;
  person: string;
  category: string;
  txn: string;
  mode: string;
  shares: number | null;
  value: number | null;
  afterPct: number | null;
  link: string;
}

export interface BoardRow {
  date: string;
  purpose: string;
  description: string;
  announced: string;
  link: string;
}

/** Net FII/FPI and DII flow for one day, in rupees crore (null when the exchange gave none). */
export interface FlowRow {
  day: string;
  fii: number | null;
  dii: number | null;
}

/** The Flows and insiders box. `insiderReach` is when the newest insider filing held was filed: a quiet box is not a quiet company when this is old. */
export interface Filings {
  insiders: InsiderRow[];
  insiderCounts: Record<string, number>;
  insiderReach: string;
  board: BoardRow[];
  flows: FlowRow[];
}

export interface PostRow {
  date: string;
  subreddit: string;
  title: string;
  snippet: string;
  score: number;
  comments: number;
  link: string;
}

export interface Tone {
  signal: string;
  confidence: number;
  reasoning: string;
  coverage: string;
}

export interface SentimentDoc {
  symbol: string;
  company: string;
  asOf: string;
  windowDays: number;
  news: { items: NewsRow[]; notes: string[]; counts: { all: number; used: number; routine: number; press: number } };
  market: MarketRow[];
  sources: SourceRow[];
  filings: Filings;
  social: { enabled: boolean; notes: string[]; items: PostRow[]; bySubreddit: Record<string, number> };
  tone: Tone | null;
  caveat: string;
}

/** A tier is A, B or C; anything else is read as C (the least trusted), never as more. */
const tierOf = (v: unknown): string => (v === 'A' || v === 'B' ? v : 'C');
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
/** A link the page may open: only http and https. */
export const safeLink = (v: unknown): string => (typeof v === 'string' && /^https?:\/\//i.test(v) ? v : '');

export function readSentiment(raw: unknown): SentimentDoc | null {
  if (!isObject(raw) || typeof raw.symbol !== 'string' || !isObject(raw.news) || !isObject(raw.social)) return null;
  const items = (Array.isArray(raw.news.items) ? raw.news.items : []).filter(isObject).map(
    (r): NewsRow => ({
      date: str(r.date), published: str(r.published), firstSeen: str(r.first_seen), publisher: str(r.publisher), source: str(r.source), sourceId: str(r.source_id), tier: tierOf(r.tier),
      title: str(r.title), link: safeLink(r.link), routine: r.routine === true, used: r.used === true,
    }),
  );
  const market = (Array.isArray(raw.market) ? raw.market : []).filter(isObject).map(
    (r): MarketRow => ({ date: str(r.date), publisher: str(r.publisher), sourceId: str(r.source_id), tier: tierOf(r.tier), title: str(r.title), link: safeLink(r.link) }),
  );
  const sources = (Array.isArray(raw.sources) ? raw.sources : []).filter(isObject).map(
    (r): SourceRow => ({ id: str(r.id), name: str(r.name), kind: str(r.kind), tier: tierOf(r.tier), status: str(r.status) || 'never tried', reason: str(r.reason), held: num(r.held), newest: str(r.newest), rowsHere: num(r.rows_here) }),
  );
  const f = isObject(raw.filings) ? raw.filings : {};
  const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isObject) : []);
  const filings: Filings = {
    insiders: list(f.insiders).map((r): InsiderRow => ({
      filed: str(r.filed), person: str(r.person), category: str(r.category), txn: str(r.txn), mode: str(r.mode), shares: numOrNull(r.shares), value: numOrNull(r.value), afterPct: numOrNull(r.after_pct), link: safeLink(r.link),
    })),
    insiderCounts: isObject(f.insider_counts) ? Object.fromEntries(Object.entries(f.insider_counts).map(([k, v]) => [k, num(v)])) : {},
    insiderReach: str(f.insider_reach),
    board: list(f.board).map((r): BoardRow => ({ date: str(r.date), purpose: str(r.purpose), description: str(r.description), announced: str(r.announced), link: safeLink(r.link) })),
    flows: list(f.flows).map((r): FlowRow => ({ day: str(r.day), fii: numOrNull(r['FII/FPI']), dii: numOrNull(r.DII) })),
  };
  const posts = (Array.isArray(raw.social.items) ? raw.social.items : []).filter(isObject).map(
    (r): PostRow => ({ date: str(r.date), subreddit: str(r.subreddit), title: str(r.title), snippet: str(r.snippet), score: num(r.score), comments: num(r.comments), link: safeLink(r.link) }),
  );
  const counts = isObject(raw.news.counts) ? raw.news.counts : {};
  const tone = isObject(raw.tone) ? { signal: str(raw.tone.signal), confidence: num(raw.tone.confidence), reasoning: str(raw.tone.reasoning), coverage: str(raw.tone.data_coverage) } : null;
  const by = isObject(raw.social.by_subreddit) ? Object.fromEntries(Object.entries(raw.social.by_subreddit).map(([k, v]) => [k, num(v)])) : {};
  return {
    symbol: raw.symbol, company: str(raw.company), asOf: str(raw.as_of), windowDays: num(raw.window_days) || 30,
    news: { items, notes: strings(raw.news.notes), counts: { all: num(counts.all), used: num(counts.used), routine: num(counts.routine), press: num(counts.press) } },
    market, sources, filings,
    social: { enabled: raw.social.enabled === true, notes: strings(raw.social.notes), items: posts, bySubreddit: by },
    tone: tone && tone.signal ? tone : null,
    caveat: str(raw.caveat),
  };
}

/** The news rows to show: all of them, or only those the analysts would use (not a routine filing, and a press item that names the company). */
export const visibleNews = (items: readonly NewsRow[], onlyUsed: boolean, sourceId = ''): NewsRow[] =>
  items.filter((r) => (!onlyUsed || r.used) && (!sourceId || r.sourceId === sourceId));

/** How a source's status is shown: ok is plain, anything the person should know about is flagged. */
export const statusClass = (status: string): 'up' | 'dn' | 'flat' => (status === 'ok' ? 'up' : status === 'off' || status === 'never tried' ? 'flat' : 'dn');

/** The citation under a row: where it came from, how far to trust the source to begin with, and when it was published and first seen. */
export function citation(r: Pick<NewsRow, 'publisher' | 'tier' | 'published' | 'firstSeen'>): string {
  const when = (iso: string) => (iso ? iso.slice(0, 16).replace('T', ' ') : '');
  const parts = [`${r.publisher}, tier ${r.tier} (a starting estimate, not a measured record)`];
  if (r.published) parts.push(`published ${when(r.published)} UTC`);
  if (r.firstSeen) parts.push(`first seen ${when(r.firstSeen)} UTC`);
  return parts.join('; ');
}

/** Rupees in the units a reader uses: crore, lakh, or plain. Missing is a dash, never zero. */
export function rupees(value: number | null): string {
  if (value === null) return '-';
  const v = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  if (v >= 1e7) return `${sign}Rs ${(v / 1e7).toFixed(2)} cr`;
  if (v >= 1e5) return `${sign}Rs ${(v / 1e5).toFixed(2)} lakh`;
  return `${sign}Rs ${Math.round(v).toLocaleString('en-IN')}`;
}

/** How the box says its insider data reaches: the newest filing's date, and a plain warning when it is more than 45 days before the page's date. */
export function insiderReachNote(reach: string, asOf: string): string {
  if (!reach) return 'No insider filings are held yet.';
  const days = Math.floor((Date.parse(asOf) - Date.parse(reach)) / 86_400_000);
  const day = reach.slice(0, 10);
  return days > 45 ? `The newest insider filing held was filed ${day}, ${days} days before this page: the exchange's feed has shown nothing newer, so an empty list here does not mean nothing happened.` : `The newest insider filing held was filed ${day}.`;
}

export function toneClass(signal: string): 'up' | 'dn' | 'flat' {
  return signal === 'bullish' ? 'up' : signal === 'bearish' ? 'dn' : 'flat';
}

/** What to tell the person when the server refuses or fails. */
export function sentimentProblem(status: number): string {
  if (status === 429) return 'Two sentiment pages are already being built; try again in a moment.';
  if (status === 400) return 'That is not a symbol the page can look up.';
  return 'The sentiment page could not be built just now (a news source may be down); try again in a minute.';
}
