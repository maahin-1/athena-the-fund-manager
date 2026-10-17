/** Watchlists as plain data: many named lists, each an ordered run of sections and symbols, with flags, columns and a sort. Every function
 * returns a new document and leaves the old one alone, so the page can undo and the store can compare. Nothing here touches the page. */

export const FLAGS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink'] as const;
export type Flag = (typeof FLAGS)[number];
export const COLUMNS = ['last', 'chg', 'chgpct', 'open', 'high', 'low', 'prev', 'volume', 'h52', 'l52', 'src'] as const;
export type Column = (typeof COLUMNS)[number];
export const COLUMN_TITLES: Record<Column, string> = {
  last: 'Last', chg: 'Chg', chgpct: 'Chg%', open: 'Open', high: 'High', low: 'Low', prev: 'Prev close', volume: 'Volume', h52: '52w High', l52: '52w Low', src: 'Source',
};
export const DEFAULT_COLUMNS: Column[] = ['last', 'chg', 'chgpct'];
export const MAX_NAME = 40;

export type Section = { t: 'section'; title: string; color?: Flag; closed?: boolean };
export type Sym = { t: 'sym'; s: string; flag?: Flag };
export type Item = Section | Sym;
export type SortKey = Column | 'sym';
export interface Sort { key: SortKey | null; dir: 1 | -1 }
export interface List { id: string; name: string; columns: Column[]; sort: Sort; items: Item[] }
export interface Doc { version: 1; active: string; lists: List[] }

export class WatchlistError extends Error {}

export const INDEX_DEFAULTS = ['NIFTY 50', 'NIFTY BANK'];

/** An index name (`NIFTY 50`, `INDIA VIX`), as against a stock or an ETF such as NIFTYBEES. Indices have no depth, volume or statements. */
export const isIndexSymbol = (symbol: string): boolean => /^NIFTY( |$)/.test(symbol) || symbol === 'INDIA VIX';

const norm = (symbol: string): string => symbol.trim().toUpperCase();
const isFlag = (x: unknown): x is Flag => typeof x === 'string' && (FLAGS as readonly string[]).includes(x);
const isColumn = (x: unknown): x is Column => typeof x === 'string' && (COLUMNS as readonly string[]).includes(x);

/** The first document: one list, Indices and Stocks sections, the symbols the layout already held. */
export function migrate(symbols: readonly string[]): Doc {
  const items: Item[] = [{ t: 'section', title: 'Indices' }, ...INDEX_DEFAULTS.map((s): Sym => ({ t: 'sym', s })), { t: 'section', title: 'Stocks' }];
  for (const s of symbols.map(norm)) if (s && !items.some((i) => i.t === 'sym' && i.s === s)) items.push({ t: 'sym', s });
  return { version: 1, active: 'l1', lists: [{ id: 'l1', name: 'Main', columns: [...DEFAULT_COLUMNS], sort: { key: null, dir: 1 }, items }] };
}

/** Check a document read from the server or the local copy. A bad one is refused whole: a half-read list would silently lose symbols. */
export function validate(raw: unknown): Doc {
  const fail = (why: string): never => {
    throw new WatchlistError(`the watchlists are not valid: ${why}`);
  };
  const doc = raw as Partial<Doc> | null;
  if (!doc || typeof doc !== 'object' || doc.version !== 1 || !Array.isArray(doc.lists) || doc.lists.length === 0) return fail('no lists');
  const ids = new Set<string>();
  const lists = doc.lists.map((l: unknown): List => {
    const x = l as Partial<List>;
    if (typeof x?.id !== 'string' || !x.id || ids.has(x.id)) return fail('a list id is missing or repeated');
    ids.add(x.id);
    if (typeof x.name !== 'string' || !x.name.trim() || x.name.length > MAX_NAME) return fail('a list name is missing or too long');
    if (!Array.isArray(x.items)) return fail('a list has no items');
    const seen = new Set<string>();
    const items = x.items.map((i: unknown): Item => {
      const it = i as { t?: string; title?: unknown; color?: unknown; closed?: unknown; s?: unknown; flag?: unknown };
      if (it?.t === 'section') {
        if (typeof it.title !== 'string') return fail('a section has no title');
        return { t: 'section', title: it.title, ...(isFlag(it.color) ? { color: it.color } : {}), ...(it.closed ? { closed: true } : {}) };
      }
      if (it?.t === 'sym') {
        const s = typeof it.s === 'string' ? norm(it.s) : '';
        if (!s || seen.has(s)) return fail('a symbol is empty or repeated in a list');
        seen.add(s);
        return { t: 'sym', s, ...(isFlag(it.flag) ? { flag: it.flag } : {}) };
      }
      return fail('an item is neither a section nor a symbol');
    });
    const columns = Array.isArray(x.columns) ? x.columns.filter(isColumn) : [];
    const sort = x.sort && (x.sort.key === null || x.sort.key === 'sym' || isColumn(x.sort.key)) ? { key: x.sort.key, dir: x.sort.dir === -1 ? -1 : 1 } : { key: null, dir: 1 };
    return { id: x.id, name: x.name.trim(), columns: columns.length ? [...new Set(columns)] : [...DEFAULT_COLUMNS], sort: sort as Sort, items };
  });
  const active = typeof doc.active === 'string' && ids.has(doc.active) ? doc.active : lists[0].id;
  return { version: 1, active, lists };
}

export function activeList(doc: Doc): List {
  return doc.lists.find((l) => l.id === doc.active) ?? doc.lists[0];
}

export const symbolsOf = (list: List): string[] => list.items.filter((i): i is Sym => i.t === 'sym').map((i) => i.s);

function withList(doc: Doc, id: string, change: (l: List) => List): Doc {
  let changed = false;
  const lists = doc.lists.map((l) => {
    if (l.id !== id) return l;
    const next = change(l);
    changed ||= next !== l;
    return next;
  });
  return changed ? { ...doc, lists } : doc; // the same document back when nothing changed, so callers can tell
}

function uniqueName(doc: Doc, wanted: string, skipId?: string): string {
  const base = wanted.trim().slice(0, MAX_NAME - 3) || 'List';
  const taken = new Set(doc.lists.filter((l) => l.id !== skipId).map((l) => l.name.toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  for (let n = 2; ; n++) if (!taken.has(`${base} ${n}`.toLowerCase())) return `${base} ${n}`;
}

export function newList(doc: Doc, name: string): Doc {
  let n = doc.lists.length + 1;
  while (doc.lists.some((l) => l.id === `l${n}`)) n++;
  const list: List = { id: `l${n}`, name: uniqueName(doc, name), columns: [...DEFAULT_COLUMNS], sort: { key: null, dir: 1 }, items: [] };
  return { ...doc, active: list.id, lists: [...doc.lists, list] };
}

export function renameList(doc: Doc, id: string, name: string): Doc {
  return withList(doc, id, (l) => ({ ...l, name: uniqueName(doc, name, id) }));
}

/** The last list cannot be deleted: there is always one to show. */
export function deleteList(doc: Doc, id: string): Doc {
  if (doc.lists.length <= 1) return doc;
  const lists = doc.lists.filter((l) => l.id !== id);
  return { ...doc, lists, active: doc.active === id ? lists[0].id : doc.active };
}

export function setActive(doc: Doc, id: string): Doc {
  return doc.lists.some((l) => l.id === id) ? { ...doc, active: id } : doc;
}

export function findList(doc: Doc, nameOrId: string): List | undefined {
  const k = nameOrId.trim().toLowerCase();
  return doc.lists.find((l) => l.id.toLowerCase() === k || l.name.toLowerCase() === k);
}

/** Add at the end of a named section (or of the list). A symbol already in the list stays where it is. */
export function addSymbol(doc: Doc, symbol: string, listId = doc.active, section?: string): Doc {
  const s = norm(symbol);
  if (!s) return doc;
  return withList(doc, listId, (l) => {
    if (l.items.some((i) => i.t === 'sym' && i.s === s)) return l;
    const items = [...l.items];
    let at = items.length;
    if (section !== undefined) {
      const start = items.findIndex((i) => i.t === 'section' && i.title.toLowerCase() === section.toLowerCase());
      if (start >= 0) {
        at = start + 1;
        while (at < items.length && items[at].t === 'sym') at++;
      }
    }
    items.splice(at, 0, { t: 'sym', s });
    return { ...l, items };
  });
}

export function removeSymbol(doc: Doc, symbol: string, listId = doc.active): Doc {
  const s = norm(symbol);
  return withList(doc, listId, (l) => ({ ...l, items: l.items.filter((i) => !(i.t === 'sym' && i.s === s)) }));
}

/** Set a symbol's flag (null clears it) in the active list. */
export function setFlag(doc: Doc, symbol: string, flag: Flag | null, listId = doc.active): Doc {
  const s = norm(symbol);
  return withList(doc, listId, (l) => ({
    ...l,
    items: l.items.map((i) => {
      if (i.t !== 'sym' || i.s !== s) return i;
      const { flag: _old, ...rest } = i;
      return flag ? { ...rest, flag } : rest;
    }),
  }));
}

/** Click on the dot: none, then each colour in turn, then none again. */
export function nextFlag(flag: Flag | undefined): Flag | null {
  if (!flag) return FLAGS[0];
  const at = FLAGS.indexOf(flag);
  return at + 1 < FLAGS.length ? FLAGS[at + 1] : null;
}

export function addSection(doc: Doc, title: string, listId = doc.active): Doc {
  const t = title.trim().slice(0, MAX_NAME);
  return t ? withList(doc, listId, (l) => ({ ...l, items: [...l.items, { t: 'section', title: t }] })) : doc;
}

function changeSection(doc: Doc, index: number, listId: string, change: (s: Section) => Section): Doc {
  return withList(doc, listId, (l) => ({ ...l, items: l.items.map((i, n) => (n === index && i.t === 'section' ? change(i) : i)) }));
}

export function toggleSection(doc: Doc, index: number, listId = doc.active): Doc {
  return changeSection(doc, index, listId, (s) => {
    const { closed, ...rest } = s;
    return closed ? rest : { ...rest, closed: true };
  });
}

export function renameSection(doc: Doc, index: number, title: string, listId = doc.active): Doc {
  const t = title.trim().slice(0, MAX_NAME);
  return t ? changeSection(doc, index, listId, (s) => ({ ...s, title: t })) : doc;
}

export function colourSection(doc: Doc, index: number, color: Flag | null, listId = doc.active): Doc {
  return changeSection(doc, index, listId, (s) => {
    const { color: _old, ...rest } = s;
    return color ? { ...rest, color } : rest;
  });
}

/** Remove a heading and keep its symbols (they join the section above, or the top of the list). */
export function removeSection(doc: Doc, index: number, listId = doc.active): Doc {
  return withList(doc, listId, (l) => (l.items[index]?.t === 'section' ? { ...l, items: l.items.filter((_, n) => n !== index) } : l));
}

/** Move the item at `from` so it sits at `to` (an index in the list as it is now). A section carries the symbols under it. */
export function moveItem(doc: Doc, from: number, to: number, listId = doc.active): Doc {
  return withList(doc, listId, (l) => {
    const items = [...l.items];
    if (from < 0 || from >= items.length || to < 0 || to > items.length) return l;
    let end = from + 1;
    if (items[from].t === 'section') while (end < items.length && items[end].t === 'sym') end++;
    if (to >= from && to <= end) return l; // dropped on itself or inside what it carries
    const block = items.splice(from, end - from);
    items.splice(to > from ? to - block.length : to, 0, ...block);
    return { ...l, items };
  });
}

export function setColumns(doc: Doc, columns: Column[], listId = doc.active): Doc {
  const unique = [...new Set(columns.filter(isColumn))];
  return unique.length ? withList(doc, listId, (l) => ({ ...l, columns: unique })) : doc;
}

/** Clicking a heading: ascending, descending, then back to the list's own order. */
export function cycleSort(doc: Doc, key: SortKey, listId = doc.active): Doc {
  return withList(doc, listId, (l) => {
    const sort: Sort = l.sort.key !== key ? { key, dir: 1 } : l.sort.dir === 1 ? { key, dir: -1 } : { key: null, dir: 1 };
    return { ...l, sort };
  });
}

/** What the sort reads for a symbol: a number, text (for the symbol column) or nothing (rows without a value go last). */
export type SortValue = (symbol: string, key: SortKey) => number | string | null;

/** The symbols in sorted order, or null when the list is shown in its own order with sections. */
export function sortedSymbols(list: List, value: SortValue): string[] | null {
  const key = list.sort.key;
  if (!key) return null;
  const dir = list.sort.dir;
  return symbolsOf(list)
    .map((s, n) => ({ s, n, v: value(s, key) }))
    .sort((a, b) => {
      if (a.v === null || b.v === null) return a.v === b.v ? a.n - b.n : a.v === null ? 1 : -1;
      const c = typeof a.v === 'string' || typeof b.v === 'string' ? String(a.v).localeCompare(String(b.v)) : a.v - b.v;
      return c !== 0 ? c * dir : a.n - b.n;
    })
    .map((x) => x.s);
}

/** Symbols that carry a flag, or all of them. */
export function filterByFlag(list: List, flag: Flag | null): Sym[] {
  return list.items.filter((i): i is Sym => i.t === 'sym' && (flag === null || i.flag === flag));
}

/** TradingView's text form: `###Section` then one comma-separated line of `NSE:` prefixed symbols. */
export function exportText(list: List): string {
  const lines: string[] = [];
  let line: string[] = [];
  const flush = (): void => {
    if (line.length) lines.push(line.join(','));
    line = [];
  };
  for (const i of list.items) {
    if (i.t === 'section') {
      flush();
      lines.push(`###${i.title}`);
    } else line.push(`NSE:${i.s}`);
  }
  flush();
  return lines.join('\n');
}

const PREFIX = /^\s*(NSE|BSE|NFO|BFO|MCX)\s*:/i;

/** Every symbol the text names, upper-case, exchange prefix dropped, each once (so the page can check them before importing). */
export function symbolsInText(text: string): string[] {
  const out = new Set<string>();
  for (const line of text.split(/\r?\n/)) {
    if (line.trim().startsWith('###')) continue;
    for (const token of line.split(',')) {
      const s = norm(token.replace(PREFIX, ''));
      if (s) out.add(s);
    }
  }
  return [...out];
}

export interface Imported { doc: Doc; added: number; skipped: string[] }

/** Read the text form (or plain symbols split by commas and new lines) into a list. `known` says which symbols the market has; others are skipped. */
export function importText(doc: Doc, text: string, known: (symbol: string) => boolean, listId = doc.active): Imported {
  let next = doc;
  let added = 0;
  const skipped: string[] = [];
  let section: string | undefined;
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith('###')) {
      section = trimmed.slice(3).trim();
      if (section) {
        const list = findListById(next, listId);
        if (!list?.items.some((i) => i.t === 'section' && i.title.toLowerCase() === section!.toLowerCase())) next = addSection(next, section, listId);
      }
      continue;
    }
    for (const token of trimmed.split(',')) {
      const s = norm(token.replace(PREFIX, ''));
      if (!s) continue;
      if (!known(s)) {
        skipped.push(s);
        continue;
      }
      const before = symbolsOf(findListById(next, listId)!).length;
      next = addSymbol(next, s, listId, section);
      if (symbolsOf(findListById(next, listId)!).length > before) added++;
    }
  }
  return { doc: next, added, skipped };
}

function findListById(doc: Doc, id: string): List | undefined {
  return doc.lists.find((l) => l.id === id);
}
