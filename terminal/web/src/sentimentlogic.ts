/** The Sentiment page's document (see `athena.sentiment.page`): read from the server, never trusted for its shape. Pure. */
export interface NewsRow {
  date: string;
  publisher: string;
  source: string;
  title: string;
  link: string;
  routine: boolean;
  used: boolean;
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
  social: { enabled: boolean; notes: string[]; items: PostRow[]; bySubreddit: Record<string, number> };
  tone: Tone | null;
  caveat: string;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
/** A link the page may open: only http and https. */
export const safeLink = (v: unknown): string => (typeof v === 'string' && /^https?:\/\//i.test(v) ? v : '');

export function readSentiment(raw: unknown): SentimentDoc | null {
  if (!isObject(raw) || typeof raw.symbol !== 'string' || !isObject(raw.news) || !isObject(raw.social)) return null;
  const items = (Array.isArray(raw.news.items) ? raw.news.items : []).filter(isObject).map(
    (r): NewsRow => ({ date: str(r.date), publisher: str(r.publisher), source: str(r.source), title: str(r.title), link: safeLink(r.link), routine: r.routine === true, used: r.used === true }),
  );
  const posts = (Array.isArray(raw.social.items) ? raw.social.items : []).filter(isObject).map(
    (r): PostRow => ({ date: str(r.date), subreddit: str(r.subreddit), title: str(r.title), snippet: str(r.snippet), score: num(r.score), comments: num(r.comments), link: safeLink(r.link) }),
  );
  const counts = isObject(raw.news.counts) ? raw.news.counts : {};
  const tone = isObject(raw.tone) ? { signal: str(raw.tone.signal), confidence: num(raw.tone.confidence), reasoning: str(raw.tone.reasoning), coverage: str(raw.tone.data_coverage) } : null;
  const by = isObject(raw.social.by_subreddit) ? Object.fromEntries(Object.entries(raw.social.by_subreddit).map(([k, v]) => [k, num(v)])) : {};
  return {
    symbol: raw.symbol, company: str(raw.company), asOf: str(raw.as_of), windowDays: num(raw.window_days) || 30,
    news: { items, notes: strings(raw.news.notes), counts: { all: num(counts.all), used: num(counts.used), routine: num(counts.routine), press: num(counts.press) } },
    social: { enabled: raw.social.enabled === true, notes: strings(raw.social.notes), items: posts, bySubreddit: by },
    tone: tone && tone.signal ? tone : null,
    caveat: str(raw.caveat),
  };
}

/** The news rows to show: all of them, or only those the analysts would use (not a routine filing, and a press item that names the company). */
export const visibleNews = (items: readonly NewsRow[], onlyUsed: boolean): NewsRow[] => (onlyUsed ? items.filter((r) => r.used) : [...items]);

export function toneClass(signal: string): 'up' | 'dn' | 'flat' {
  return signal === 'bullish' ? 'up' : signal === 'bearish' ? 'dn' : 'flat';
}

/** What to tell the person when the server refuses or fails. */
export function sentimentProblem(status: number): string {
  if (status === 429) return 'Two sentiment pages are already being built; try again in a moment.';
  if (status === 400) return 'That is not a symbol the page can look up.';
  return 'The sentiment page could not be built just now (a news source may be down); try again in a minute.';
}
