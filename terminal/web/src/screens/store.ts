import { COLUMNS, DEFAULT_COLUMNS, noFilters, type Filters, type Limits } from '../screenlogic';

/** Saved screens: one document on the server (`/api/screens`), as the watchlists are, so they follow the user across browsers and layouts. A screen is a
 *  name and what the Screen tab needs to show the same table again: the query, the filters, the columns and the sort. */

export const MAX_SCREENS = 100;
export const MAX_NAME = 60;
const DEFAULT_SORT = 'traded_value_cr';

export interface SavedScreen {
  name: string;
  query: string;
  filters: Filters;
  columns: string[];
  sortBy: string;
  descending: boolean;
}

export interface ScreensDoc {
  version: 1;
  screens: SavedScreen[];
}

export class ScreensError extends Error {}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

function validLimits(raw: unknown): Limits {
  const out: Limits = {};
  if (raw && typeof raw === 'object') {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      const ok = Array.isArray(v) && v.length === 2 && v.every((n) => n === null || (typeof n === 'number' && Number.isFinite(n)));
      if (ok) out[k] = v as [number | null, number | null];
    }
  }
  return out;
}

function validFilters(raw: unknown): Filters {
  const base = noFilters();
  if (!raw || typeof raw !== 'object') return base;
  const f = raw as Record<string, unknown>;
  const text = (v: unknown, fallback: string): string => (typeof v === 'string' ? v : fallback);
  return {
    kinds: Array.isArray(f.kinds) ? strings(f.kinds) : base.kinds,
    text: text(f.text, base.text),
    ranges: validLimits(f.ranges),
    trends: strings(f.trends),
    sector: text(f.sector, ''),
    industry: text(f.industry, ''),
    index: text(f.index, ''),
    cleanOnly: f.cleanOnly === true,
  };
}

function validScreen(raw: unknown): SavedScreen {
  if (!raw || typeof raw !== 'object') throw new ScreensError('a saved screen is not an object');
  const s = raw as Record<string, unknown>;
  const name = typeof s.name === 'string' ? s.name.trim() : '';
  if (name.length < 1 || name.length > MAX_NAME) throw new ScreensError(`a screen's name must be 1 to ${MAX_NAME} characters`);
  if (typeof s.query !== 'string') throw new ScreensError(`the query of "${name}" must be text`);
  const columns = strings(s.columns).filter((c) => c in COLUMNS);
  return {
    name,
    query: s.query,
    filters: validFilters(s.filters),
    columns: columns.length ? columns : [...DEFAULT_COLUMNS],
    sortBy: typeof s.sortBy === 'string' && s.sortBy in COLUMNS ? s.sortBy : DEFAULT_SORT,
    descending: typeof s.descending === 'boolean' ? s.descending : true,
  };
}

/** The document as a page can trust it, or a `ScreensError` saying what is wrong. Unknown columns are dropped and an unknown sort falls back, so a
 *  screen saved by another version still opens; a query is kept as written even when it does not parse. */
export function validateScreens(raw: unknown): ScreensDoc {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new ScreensError('the saved screens are not a document');
  const d = raw as Record<string, unknown>;
  if (d.version !== 1) throw new ScreensError('the saved screens have an unknown version');
  const list = Array.isArray(d.screens) ? d.screens : [];
  if (list.length > MAX_SCREENS) throw new ScreensError(`at most ${MAX_SCREENS} screens can be saved`);
  const screens = list.map(validScreen);
  const seen = new Set<string>();
  for (const s of screens) {
    const key = s.name.toLowerCase();
    if (seen.has(key)) throw new ScreensError(`the name "${s.name}" is used twice`);
    seen.add(key);
  }
  return { version: 1, screens };
}

/** A screen saved under a name replaces the one of that name (any case) in its place; a new name goes last. */
export function saveScreen(doc: ScreensDoc, screen: SavedScreen): ScreensDoc {
  const clean = validScreen(screen);
  const at = doc.screens.findIndex((s) => s.name.toLowerCase() === clean.name.toLowerCase());
  if (at < 0 && doc.screens.length >= MAX_SCREENS) throw new ScreensError(`at most ${MAX_SCREENS} screens can be saved`);
  const screens = [...doc.screens];
  if (at < 0) screens.push(clean);
  else screens[at] = clean;
  return { version: 1, screens };
}

export function deleteScreen(doc: ScreensDoc, name: string): ScreensDoc {
  return { version: 1, screens: doc.screens.filter((s) => s.name.toLowerCase() !== name.toLowerCase()) };
}

export interface ScreensIO {
  /** The saved document, or null when none is saved yet; throws when the server cannot be reached. */
  load(): Promise<unknown | null>;
  save(body: string): Promise<void>;
}

export function serverScreensIO(fetcher: typeof fetch = fetch): ScreensIO {
  return {
    async load() {
      const r = await fetcher('/api/screens');
      if (r.status === 204 || r.status === 404) return null;
      if (!r.ok) throw new Error(`screens: HTTP ${r.status}`);
      return r.json();
    },
    async save(body) {
      const r = await fetcher('/api/screens', { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
      if (!r.ok) throw new Error(`screens: HTTP ${r.status}`);
    },
  };
}

/** The page's view of the saved screens. A server that cannot be reached, or a document that cannot be read, leaves it empty with `problem` set; a
 *  change is kept for the session even when it could not be saved (the save then throws, for the page to say so). */
export class SavedScreens {
  doc: ScreensDoc = { version: 1, screens: [] };
  problem: string | null = null;

  constructor(private io: ScreensIO) {}

  async load(): Promise<void> {
    let raw: unknown;
    try {
      raw = await this.io.load();
    } catch {
      this.problem = 'Saved screens could not be loaded: the server did not answer.';
      return;
    }
    if (raw === null) return;
    try {
      this.doc = validateScreens(raw);
      this.problem = null;
    } catch (e) {
      this.problem = `Saved screens could not be read (${e instanceof Error ? e.message : 'unknown'}); starting with none.`;
    }
  }

  names(): string[] {
    return this.doc.screens.map((s) => s.name);
  }

  get(name: string): SavedScreen | undefined {
    return this.doc.screens.find((s) => s.name.toLowerCase() === name.toLowerCase());
  }

  async save(screen: SavedScreen): Promise<void> {
    this.doc = saveScreen(this.doc, screen);
    await this.io.save(JSON.stringify(this.doc));
  }

  async remove(name: string): Promise<void> {
    this.doc = deleteScreen(this.doc, name);
    await this.io.save(JSON.stringify(this.doc));
  }
}
