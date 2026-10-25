/** The notebook: the owner's own notes on symbols, as plain data. Every operation returns a new list and leaves the old one alone. Nothing here touches the page. */

export interface Note {
  id: string;
  symbol: string;
  text: string;
  tag: string;
  created: number; // ms since 1970
  updated: number;
}

export interface NotebookDoc {
  version: 1;
  notes: readonly Note[];
}

export const MAX_NOTES = 1000;
export const MAX_TEXT = 2000;
export const MAX_TAG = 24;

export class NoteError extends Error {}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const SYMBOL = /^[A-Z0-9&^ -]{1,24}$/;

let counter = 0;
const freshId = (now: number): string => {
  counter += 1;
  return `n${now.toString(36)}${counter.toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
};

/** A symbol as the notebook keeps it: capitals, digits, `&`, `^`, space and `-`. Anything else is refused. */
export function cleanSymbol(symbol: string): string {
  const s = symbol.trim().toUpperCase();
  if (!SYMBOL.test(s)) throw new NoteError('a note needs a symbol of capitals, digits, & ^ space or -');
  return s;
}

export function cleanTag(tag: string): string {
  return tag.trim().toLowerCase().replace(/\s+/g, '-').slice(0, MAX_TAG);
}

export function addNote(list: readonly Note[], symbol: string, text: string, tag = '', now = Date.now()): Note[] {
  const body = text.trim();
  if (!body) throw new NoteError('a note cannot be empty');
  if (body.length > MAX_TEXT) throw new NoteError(`a note is at most ${MAX_TEXT} characters`);
  if (list.length >= MAX_NOTES) throw new NoteError(`the notebook holds at most ${MAX_NOTES} notes`);
  return [...list, { id: freshId(now), symbol: cleanSymbol(symbol), text: body, tag: cleanTag(tag), created: now, updated: now }];
}

export function editNote(list: readonly Note[], id: string, text: string, tag: string, now = Date.now()): Note[] {
  const body = text.trim();
  if (!body) throw new NoteError('a note cannot be empty');
  if (body.length > MAX_TEXT) throw new NoteError(`a note is at most ${MAX_TEXT} characters`);
  return list.map((n) => (n.id === id ? { ...n, text: body, tag: cleanTag(tag), updated: now } : n));
}

export const removeNote = (list: readonly Note[], id: string): Note[] => list.filter((n) => n.id !== id);

export interface Query {
  symbol?: string;
  tag?: string;
  text?: string;
}

/** The notes that match, newest first. Text matches the note, its tag and its symbol, ignoring case. */
export function search(list: readonly Note[], q: Query = {}): Note[] {
  const text = (q.text ?? '').trim().toLowerCase();
  return list
    .filter((n) => (!q.symbol || n.symbol === q.symbol.toUpperCase()) && (!q.tag || n.tag === q.tag) && (!text || `${n.text} ${n.tag} ${n.symbol}`.toLowerCase().includes(text)))
    .sort((a, b) => b.updated - a.updated || a.id.localeCompare(b.id));
}

/** The tags in use, with how many notes carry each, most used first. */
export function tags(list: readonly Note[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const n of list) if (n.tag) counts.set(n.tag, (counts.get(n.tag) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** The symbols that have notes, with their counts, most noted first. */
export function symbols(list: readonly Note[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const n of list) counts.set(n.symbol, (counts.get(n.symbol) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

const day = (ms: number): string => new Date(ms).toISOString().slice(0, 10);

/** The notes as a Markdown document, grouped by symbol, oldest first within a symbol. */
export function toMarkdown(list: readonly Note[]): string {
  const out = ['# Notebook', ''];
  for (const [symbol] of symbols(list).sort((a, b) => a[0].localeCompare(b[0]))) {
    out.push(`## ${symbol}`, '');
    for (const n of list.filter((x) => x.symbol === symbol).sort((a, b) => a.created - b.created)) {
      out.push(`- ${day(n.created)}${n.tag ? ` [${n.tag}]` : ''}: ${n.text.replace(/\n+/g, ' ')}`);
    }
    out.push('');
  }
  return out.join('\n');
}

/** Read a saved notebook. Anything of the wrong shape is refused with a reason; a note that is not a note is left out, never guessed at. */
export function readDoc(raw: unknown): NotebookDoc {
  if (!isObject(raw) || raw.version !== 1 || !Array.isArray(raw.notes)) throw new NoteError('not a notebook document');
  const seen = new Set<string>();
  const notes: Note[] = [];
  for (const item of raw.notes.slice(0, MAX_NOTES)) {
    if (!isObject(item) || typeof item.id !== 'string' || typeof item.symbol !== 'string' || typeof item.text !== 'string' || seen.has(item.id)) continue;
    const symbol = item.symbol.trim().toUpperCase();
    const text = item.text.trim();
    if (!SYMBOL.test(symbol) || !text || text.length > MAX_TEXT) continue;
    seen.add(item.id);
    const created = typeof item.created === 'number' && Number.isFinite(item.created) ? item.created : 0;
    const updated = typeof item.updated === 'number' && Number.isFinite(item.updated) ? item.updated : created;
    notes.push({ id: item.id, symbol, text, tag: typeof item.tag === 'string' ? cleanTag(item.tag) : '', created, updated });
  }
  return { version: 1, notes };
}

export const serializeDoc = (notes: readonly Note[]): string => JSON.stringify({ version: 1, notes } satisfies NotebookDoc);
