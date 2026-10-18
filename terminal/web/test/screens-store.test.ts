import { describe, expect, it } from 'vitest';
import { filterRows, noFilters, COLUMNS, DEFAULT_COLUMNS, type Row } from '../src/screenlogic';
import { MAX_NAME, MAX_SCREENS, SavedScreens, ScreensError, deleteScreen, saveScreen, validateScreens, type ScreensDoc, type ScreensIO } from '../src/screens/store';

const screen = (name: string, extra: object = {}) => ({ name, query: 'pe < 20', filters: noFilters(), columns: ['symbol', 'pe'], sortBy: 'pe', descending: false, ...extra });
const doc = (...names: string[]): ScreensDoc => ({ version: 1, screens: names.map((n) => screen(n)) });

describe('the screen filters for industry and index', () => {
  const rows: Row[] = [
    { symbol: 'A', kind: 'stock', industry: 'Banks', indices: 'NIFTY 50|NIFTY BANK' },
    { symbol: 'B', kind: 'stock', industry: 'Software', indices: 'NIFTY IT' },
    { symbol: 'C', kind: 'stock', industry: null, indices: null },
  ];
  it('keep the rows of an industry, whatever the case, and those in an index', () => {
    expect(filterRows(rows, { ...noFilters(), industry: 'banks' }).map((r) => r.symbol)).toEqual(['A']);
    expect(filterRows(rows, { ...noFilters(), index: 'nifty bank' }).map((r) => r.symbol)).toEqual(['A']);
    expect(filterRows(rows, { ...noFilters(), index: 'NIFTY IT', industry: 'Software' }).map((r) => r.symbol)).toEqual(['B']);
    expect(filterRows(rows, noFilters())).toHaveLength(3); // no limit: every stock
  });

  it('are shown as columns too', () => {
    expect(COLUMNS.industry?.[0]).toBe('Industry');
    expect(COLUMNS.indices?.[0]).toBe('Indices');
    expect(DEFAULT_COLUMNS).toContain('symbol');
  });
});

describe('the saved screens document', () => {
  it('accepts what it wrote and fills what an older or hand-made one lacks', () => {
    expect(validateScreens(doc('Value', 'Growth')).screens.map((s) => s.name)).toEqual(['Value', 'Growth']);
    const lean = validateScreens({ version: 1, screens: [{ name: 'Lean', query: 'roe > 15' }] }).screens[0];
    expect(lean.columns).toEqual(DEFAULT_COLUMNS);
    expect(lean.sortBy).toBe('traded_value_cr');
    expect(lean.filters).toEqual(noFilters());
  });

  it('refuses what is not a document, a bad name or a repeated name', () => {
    expect(() => validateScreens([])).toThrow(ScreensError);
    expect(() => validateScreens({ version: 2, screens: [] })).toThrow(/version/);
    expect(() => validateScreens({ version: 1, screens: [screen('')] })).toThrow(/name/);
    expect(() => validateScreens({ version: 1, screens: [screen('x'.repeat(MAX_NAME + 1))] })).toThrow(/name/);
    expect(() => validateScreens({ version: 1, screens: [screen('Same'), screen('same')] })).toThrow(/twice/);
    expect(() => validateScreens({ version: 1, screens: [{ name: 'q', query: 5 }] })).toThrow(/query/);
    expect(() => validateScreens({ version: 1, screens: Array.from({ length: MAX_SCREENS + 1 }, (_, i) => screen(`S${i}`)) })).toThrow(/at most/);
  });

  it('drops unknown columns and falls back for an unknown sort, so a screen from another version still opens', () => {
    const s = validateScreens({ version: 1, screens: [screen('X', { columns: ['symbol', 'nonsense', 'pe'], sortBy: 'nonsense' })] }).screens[0];
    expect(s.columns).toEqual(['symbol', 'pe']);
    expect(s.sortBy).toBe('traded_value_cr');
    expect(validateScreens({ version: 1, screens: [screen('X', { columns: ['nonsense'] })] }).screens[0].columns).toEqual(DEFAULT_COLUMNS);
  });

  it('keeps a query that does not parse, for the page to show its error', () => {
    expect(validateScreens({ version: 1, screens: [screen('Broken', { query: 'pee <' })] }).screens[0].query).toBe('pee <');
  });
});

describe('saving and deleting', () => {
  it('replace a screen of the same name, whatever the case, in its place, and add a new one at the end', () => {
    const next = saveScreen(doc('A', 'B', 'C'), screen('b', { query: 'roe > 20' }));
    expect(next.screens.map((s) => s.name)).toEqual(['A', 'b', 'C']);
    expect(next.screens[1].query).toBe('roe > 20');
    expect(saveScreen(doc('A'), screen('Z')).screens.map((s) => s.name)).toEqual(['A', 'Z']);
  });

  it('refuse the hundred and first', () => {
    const full = doc(...Array.from({ length: MAX_SCREENS }, (_, i) => `S${i}`));
    expect(() => saveScreen(full, screen('One more'))).toThrow(/at most/);
    expect(saveScreen(full, screen('s5')).screens).toHaveLength(MAX_SCREENS); // a replacement is not one more
  });

  it('delete by name', () => {
    expect(deleteScreen(doc('A', 'B'), 'a').screens.map((s) => s.name)).toEqual(['B']);
    expect(deleteScreen(doc('A'), 'nope').screens).toHaveLength(1);
  });
});

describe('the store', () => {
  const memory = (initial: unknown | null): ScreensIO & { saved: string[] } => {
    const io = { saved: [] as string[], async load() { return initial; }, async save(body: string) { io.saved.push(body); } };
    return io;
  };

  it('loads the document, saves each change, and starts empty when nothing is saved', async () => {
    const io = memory(doc('A'));
    const store = new SavedScreens(io);
    await store.load();
    expect(store.names()).toEqual(['A']);
    await store.save(screen('B'));
    expect(JSON.parse(io.saved[0]).screens.map((s: { name: string }) => s.name)).toEqual(['A', 'B']);
    await store.remove('A');
    expect(store.names()).toEqual(['B']);
    expect(store.get('b')?.query).toBe('pe < 20');
    const empty = new SavedScreens(memory(null));
    await empty.load();
    expect(empty.names()).toEqual([]);
  });

  it('keeps working when the server cannot be reached or holds a damaged document, and says so', async () => {
    const down = new SavedScreens({ async load() { throw new Error('offline'); }, async save() { throw new Error('offline'); } });
    await down.load();
    expect(down.problem).toMatch(/could not be loaded/);
    await expect(down.save(screen('A'))).rejects.toThrow();
    expect(down.names()).toEqual(['A']); // kept in memory for this session
    const damaged = new SavedScreens(memory({ version: 9 }));
    await damaged.load();
    expect(damaged.problem).toMatch(/could not be read/);
    expect(damaged.names()).toEqual([]);
  });
});
