// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { addNote, cleanSymbol, editNote, MAX_NOTES, MAX_TEXT, NoteError, readDoc, removeNote, search, serializeDoc, symbols, tags, toMarkdown, type Note } from '../src/notes/model';
import { Notebook, serverNotebookIO, type NotebookIO } from '../src/notes/store';

const T0 = Date.UTC(2026, 9, 25, 10, 0);

describe('the notebook model', () => {
  it('adds a trimmed note with a clean symbol and tag, and refuses an empty, a long or a nonsense one', () => {
    const list = addNote([], ' tcs ', '  Results beat the range.  ', ' Earnings  Watch ', T0);
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ symbol: 'TCS', text: 'Results beat the range.', tag: 'earnings-watch', created: T0, updated: T0 });
    expect(() => addNote([], 'TCS', '   ')).toThrow(NoteError);
    expect(() => addNote([], 'TCS', 'x'.repeat(MAX_TEXT + 1))).toThrow(/at most 2000/);
    expect(() => addNote([], 'tcs; drop', 'text')).toThrow(/symbol/);
    expect(cleanSymbol('m&m')).toBe('M&M');
    const full = Array.from({ length: MAX_NOTES }, (_, i) => ({ id: `n${i}`, symbol: 'A', text: 't', tag: '', created: 0, updated: 0 }));
    expect(() => addNote(full, 'TCS', 'one more')).toThrow(/at most 1000/);
  });

  it('edits and removes by id and leaves the old list alone', () => {
    const one = addNote([], 'TCS', 'first', 'a', T0);
    const edited = editNote(one, one[0].id, 'second', 'B', T0 + 1000);
    expect(edited[0]).toMatchObject({ text: 'second', tag: 'b', created: T0, updated: T0 + 1000 });
    expect(one[0].text).toBe('first');
    expect(() => editNote(one, one[0].id, '  ', '')).toThrow(NoteError);
    expect(removeNote(edited, edited[0].id)).toEqual([]);
    expect(removeNote(edited, 'nothing')).toHaveLength(1);
  });

  const book: Note[] = [
    { id: 'a', symbol: 'TCS', text: 'Margin guidance is the thing to watch', tag: 'margin', created: T0, updated: T0 },
    { id: 'b', symbol: 'INFY', text: 'Deal wins strong', tag: 'deals', created: T0 + 1000, updated: T0 + 5000 },
    { id: 'c', symbol: 'TCS', text: 'Buyback announced', tag: 'deals', created: T0 + 2000, updated: T0 + 2000 },
  ];

  it('searches by symbol, tag and text, newest first, ignoring case', () => {
    expect(search(book).map((n) => n.id)).toEqual(['b', 'c', 'a']);
    expect(search(book, { symbol: 'tcs' }).map((n) => n.id)).toEqual(['c', 'a']);
    expect(search(book, { tag: 'deals' }).map((n) => n.id)).toEqual(['b', 'c']);
    expect(search(book, { text: 'MARGIN' }).map((n) => n.id)).toEqual(['a']);
    expect(search(book, { text: 'infy' }).map((n) => n.id)).toEqual(['b']); // the symbol counts as text
    expect(search(book, { symbol: 'TCS', tag: 'deals', text: 'buy' }).map((n) => n.id)).toEqual(['c']);
    expect(search(book, { symbol: 'NOPE' })).toEqual([]);
  });

  it('counts tags and symbols, most used first', () => {
    expect(tags(book)).toEqual([['deals', 2], ['margin', 1]]);
    expect(symbols(book)).toEqual([['TCS', 2], ['INFY', 1]]);
  });

  it('writes Markdown grouped by symbol, oldest first, with the tag and the date', () => {
    const md = toMarkdown(book);
    expect(md.startsWith('# Notebook')).toBe(true);
    expect(md.indexOf('## INFY')).toBeLessThan(md.indexOf('## TCS'));
    expect(md).toContain('- 2026-10-25 [margin]: Margin guidance is the thing to watch');
    expect(md.indexOf('Margin guidance')).toBeLessThan(md.indexOf('Buyback announced'));
  });

  it('reads a saved document, leaving out what is not a note and never guessing at it', () => {
    const doc = readDoc({
      version: 1,
      notes: [book[0], { id: 'x' }, 5, { ...book[1], id: 'a' }, { id: 'e', symbol: 'bad;sym', text: 't', tag: '', created: 0, updated: 0 }, { id: 'f', symbol: 'tcs', text: '  ', tag: '', created: 0, updated: 0 }, { id: 'g', symbol: 'tcs', text: 'ok', tag: 'T  G', created: 5 }],
    });
    expect(doc.notes.map((n) => n.id)).toEqual(['a', 'g']); // a duplicate id, a bad symbol and an empty text are dropped
    expect(doc.notes[1]).toMatchObject({ symbol: 'TCS', tag: 't-g', created: 5, updated: 5 });
    expect(() => readDoc(null)).toThrow(NoteError);
    expect(() => readDoc({ version: 2, notes: [] })).toThrow(NoteError);
    expect(readDoc(JSON.parse(serializeDoc(book))).notes).toEqual(book);
  });
});

describe('the notebook store', () => {
  let saved: string[];
  let io: NotebookIO;
  beforeEach(() => {
    vi.useFakeTimers();
    saved = [];
    io = { load: async () => null, save: async (body) => void saved.push(body) };
  });

  it('keeps a change at once and saves it once, 400 ms after the last change', async () => {
    const book = new Notebook(io);
    await book.load();
    const seen: number[] = [];
    book.subscribe(() => seen.push(book.notes.length));
    book.add('TCS', 'one');
    book.add('INFY', 'two', 'x');
    expect(book.notes).toHaveLength(2);
    expect(saved).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(399);
    expect(saved).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(2);
    expect(saved).toHaveLength(1);
    expect(readDoc(JSON.parse(saved[0])).notes.map((n) => n.symbol)).toEqual(['TCS', 'INFY']);
    book.remove(book.notes[0].id);
    await book.flush();
    expect(saved).toHaveLength(2);
    expect(seen.length).toBeGreaterThan(2);
  });

  it('loads what was saved, and says so when the server cannot be reached or the document is unreadable, keeping what is made meanwhile', async () => {
    const good = new Notebook({ load: async () => JSON.parse(serializeDoc(addNote([], 'TCS', 'kept'))), save: async () => {} });
    await good.load();
    expect(good.notes.map((n) => n.text)).toEqual(['kept']);
    expect(good.problem).toBeNull();
    const down = new Notebook({ load: async () => { throw new Error('offline'); }, save: async () => { throw new Error('offline'); } });
    await down.load();
    expect(down.problem).toContain('could not be loaded');
    down.add('TCS', 'made offline');
    await down.flush();
    expect(down.notes).toHaveLength(1);
    expect(down.problem).toContain('could not be saved');
    const garbled = new Notebook({ load: async () => ({ version: 9 }), save: async () => {} });
    await garbled.load();
    expect(garbled.problem).toContain('could not be read');
  });

  it('talks to the notebook route: no document is not an error, and a failure is', async () => {
    const answer = (status: number, body: unknown = null) => vi.fn(async () => new Response(body === null ? null : JSON.stringify(body), { status }));
    const none = serverNotebookIO(answer(204) as unknown as typeof fetch);
    expect(await none.load()).toBeNull();
    const there = serverNotebookIO(answer(200, { version: 1, notes: [] }) as unknown as typeof fetch);
    expect(await there.load()).toEqual({ version: 1, notes: [] });
    await expect(serverNotebookIO(answer(500) as unknown as typeof fetch).load()).rejects.toThrow('HTTP 500');
    const put = answer(204);
    await serverNotebookIO(put as unknown as typeof fetch).save('{}');
    expect(put).toHaveBeenCalledWith('/api/notebook', expect.objectContaining({ method: 'PUT', body: '{}' }));
    await expect(serverNotebookIO(answer(413) as unknown as typeof fetch).save('{}')).rejects.toThrow('HTTP 413');
  });
});
