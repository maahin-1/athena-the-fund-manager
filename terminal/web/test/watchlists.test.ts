import { describe, expect, it } from 'vitest';
import {
  ADVANCED_COLUMNS, BASIC_COLUMNS, COLUMN_TITLES, COLUMNS, activeList, addSection, addSymbol, colourSection, cycleSort, deleteList, exportText, filterByFlag, findList, importText, migrate, moveItem, newList,
  nextFlag, removeSection, removeSymbol, renameList, renameSection, setActive, setColumns, setFlag, sortedSymbols, symbolsOf, toggleSection, validate,
  WatchlistError, type Doc, type Item,
} from '../src/watchlists/model';

const titles = (list: Item[]): string[] => list.map((i) => (i.t === 'section' ? `#${i.title}` : i.s));
const doc = (): Doc => migrate(['sbin', 'TCS', 'sbin', ' infy ']);
const items = (d: Doc): string[] => titles(activeList(d).items);
/** A document whose only list holds exactly these items. */
const with_ = (list: Item[]): Doc => {
  const d = doc();
  return { ...d, lists: [{ ...d.lists[0], items: list }] };
};
const abc: Item[] = [{ t: 'section', title: 'A' }, { t: 'sym', s: 'X' }, { t: 'sym', s: 'Y' }, { t: 'section', title: 'B' }, { t: 'sym', s: 'Z' }];

describe('first document', () => {
  it('has one list with Indices and Stocks sections, upper-case symbols and no repeats', () => {
    const d = doc();
    expect(d.lists).toHaveLength(1);
    expect(d.lists[0].name).toBe('Main');
    expect(items(d)).toEqual(['#Indices', 'NIFTY 50', 'NIFTY BANK', '#Stocks', 'SBIN', 'TCS', 'INFY']);
  });
});

describe('validate', () => {
  it('accepts what migrate wrote, after a JSON round trip', () => {
    const d = doc();
    expect(validate(JSON.parse(JSON.stringify(d)))).toEqual(d);
  });
  it('refuses a document whole when one list is broken, with the reason', () => {
    expect(() => validate(null)).toThrow(WatchlistError);
    expect(() => validate({ version: 2, lists: [] })).toThrow(/no lists/);
    const repeated = doc();
    (repeated.lists[0].items as unknown[]).push({ t: 'sym', s: 'SBIN' });
    expect(() => validate(repeated)).toThrow(/repeated/);
    const odd = doc();
    (odd.lists[0].items as unknown[]).push({ t: 'banner' });
    expect(() => validate(odd)).toThrow(/neither/);
    expect(() => validate({ ...doc(), lists: [{ ...doc().lists[0], name: ' ' }] })).toThrow(/name/);
    expect(() => validate({ ...doc(), lists: [doc().lists[0], doc().lists[0]] })).toThrow(/id/);
  });
  it('repairs the small things: a missing active list, unknown columns, an unknown flag', () => {
    const d = JSON.parse(JSON.stringify(doc()));
    d.active = 'gone';
    d.lists[0].columns = ['last', 'nonsense', 'last'];
    d.lists[0].items[4].flag = 'teal';
    const v = validate(d);
    expect(v.active).toBe('l1');
    expect(v.lists[0].columns).toEqual(['last']);
    expect(v.lists[0].items[4]).toEqual({ t: 'sym', s: 'SBIN' });
  });
});

describe('symbols', () => {
  it('adds at the end or into a named section, once, and removes', () => {
    let d = addSymbol(doc(), 'hdfcbank');
    expect(items(d).slice(-1)).toEqual(['HDFCBANK']);
    d = addSymbol(d, 'nifty it', undefined, 'indices');
    expect(items(d).slice(0, 4)).toEqual(['#Indices', 'NIFTY 50', 'NIFTY BANK', 'NIFTY IT']);
    expect(addSymbol(d, 'SBIN')).toEqual(d); // already there: nothing changes
    expect(addSymbol(d, '  ')).toBe(d);
    expect(items(removeSymbol(d, 'tcs'))).not.toContain('TCS');
  });
  it('keeps one symbol per list, but the same symbol may be in two lists', () => {
    const d = addSymbol(newList(doc(), 'Second'), 'SBIN');
    expect(symbolsOf(activeList(d))).toEqual(['SBIN']);
    expect(symbolsOf(d.lists[0])).toContain('SBIN');
  });
  it('flags cycle through the colours and back to none, and clearing drops the field', () => {
    expect(nextFlag(undefined)).toBe('red');
    expect(nextFlag('red')).toBe('orange');
    expect(nextFlag('pink')).toBeNull();
    let d = setFlag(doc(), 'sbin', 'green');
    expect(activeList(d).items.find((i) => i.t === 'sym' && i.s === 'SBIN')).toEqual({ t: 'sym', s: 'SBIN', flag: 'green' });
    expect(filterByFlag(activeList(d), 'green').map((x) => x.s)).toEqual(['SBIN']);
    expect(filterByFlag(activeList(d), null)).toHaveLength(5);
    d = setFlag(d, 'SBIN', null);
    expect(activeList(d).items.find((i) => i.t === 'sym' && i.s === 'SBIN')).toEqual({ t: 'sym', s: 'SBIN' });
  });
});

describe('sections', () => {
  it('can be added, collapsed, renamed, coloured and removed leaving their symbols', () => {
    let d = addSection(doc(), 'Banks');
    expect(items(d).slice(-1)).toEqual(['#Banks']);
    expect(addSection(d, '  ')).toBe(d);
    d = toggleSection(d, 3);
    expect(activeList(d).items[3]).toEqual({ t: 'section', title: 'Stocks', closed: true });
    d = toggleSection(d, 3);
    expect(activeList(d).items[3]).toEqual({ t: 'section', title: 'Stocks' });
    d = colourSection(renameSection(d, 3, 'Equities'), 3, 'blue');
    expect(activeList(d).items[3]).toEqual({ t: 'section', title: 'Equities', color: 'blue' });
    d = removeSection(d, 3);
    expect(items(d)).toEqual(['#Indices', 'NIFTY 50', 'NIFTY BANK', 'SBIN', 'TCS', 'INFY', '#Banks']);
    expect(items(removeSection(d, 1))).toEqual(items(d)); // index 1 is a symbol, not a heading: nothing is removed
  });
});

describe('moving', () => {
  it('moves a symbol before the item at the target index, down and up', () => {
    expect(items(moveItem(with_(abc), 1, 4))).toEqual(['#A', 'Y', '#B', 'X', 'Z']);
    expect(items(moveItem(with_(abc), 2, 0))).toEqual(['Y', '#A', 'X', '#B', 'Z']);
    expect(items(moveItem(with_(abc), 4, 1))).toEqual(['#A', 'Z', 'X', 'Y', '#B']);
  });
  it('moves a section with the symbols under it', () => {
    expect(items(moveItem(with_(abc), 0, 5))).toEqual(['#B', 'Z', '#A', 'X', 'Y']);
    expect(items(moveItem(with_(abc), 3, 0))).toEqual(['#B', 'Z', '#A', 'X', 'Y']);
  });
  it('leaves the list alone when dropped on itself, inside what it carries, or out of range', () => {
    const d = with_(abc);
    expect(items(moveItem(d, 0, 2))).toEqual(items(d));
    expect(items(moveItem(d, 1, 2))).toEqual(items(d));
    expect(items(moveItem(d, 9, 0))).toEqual(items(d));
    expect(items(moveItem(d, 1, 9))).toEqual(items(d));
  });
});

describe('sorting', () => {
  const last: Record<string, number | null> = { SBIN: 800, TCS: 3000, INFY: null, 'NIFTY 50': 23000, 'NIFTY BANK': 52000 };
  const value = (s: string, key: string): number | string | null => (key === 'sym' ? s : last[s]);
  it('clicking a heading goes ascending, descending, then back to the list order', () => {
    let d = cycleSort(doc(), 'last');
    expect(activeList(d).sort).toEqual({ key: 'last', dir: 1 });
    d = cycleSort(d, 'last');
    expect(activeList(d).sort).toEqual({ key: 'last', dir: -1 });
    d = cycleSort(d, 'last');
    expect(activeList(d).sort).toEqual({ key: null, dir: 1 });
    expect(sortedSymbols(activeList(d), value)).toBeNull();
    expect(activeList(cycleSort(cycleSort(doc(), 'last'), 'chg')).sort).toEqual({ key: 'chg', dir: 1 }); // another heading starts ascending
  });
  it('puts rows without a value last in both directions', () => {
    const asc = cycleSort(doc(), 'last');
    expect(sortedSymbols(activeList(asc), value)).toEqual(['SBIN', 'TCS', 'NIFTY 50', 'NIFTY BANK', 'INFY']);
    expect(sortedSymbols(activeList(cycleSort(asc, 'last')), value)).toEqual(['NIFTY BANK', 'NIFTY 50', 'TCS', 'SBIN', 'INFY']);
  });
  it('sorts the symbol column as text', () => {
    expect(sortedSymbols(activeList(cycleSort(doc(), 'sym')), value)).toEqual(['INFY', 'NIFTY 50', 'NIFTY BANK', 'SBIN', 'TCS']);
  });
});

describe('lists', () => {
  it('a new list becomes active and gets a name no other list has', () => {
    let d = newList(doc(), 'Main');
    expect(d.lists.map((l) => l.name)).toEqual(['Main', 'Main 2']);
    expect(d.active).toBe(d.lists[1].id);
    d = newList(d, 'main');
    expect(d.lists[2].name).toBe('main 3');
    expect(new Set(d.lists.map((l) => l.id)).size).toBe(3);
    expect(newList(doc(), '   ').lists[1].name).toBe('List');
    expect(newList(doc(), 'x'.repeat(80)).lists[1].name.length).toBeLessThanOrEqual(40);
  });
  it('renaming keeps a list its own name but not another list\'s', () => {
    const d = newList(doc(), 'Second');
    expect(renameList(d, 'l2', 'Second').lists[1].name).toBe('Second');
    expect(renameList(d, 'l2', 'main').lists[1].name).toBe('main 2');
  });
  it('deleting switches the active list, and the last list stays', () => {
    const d = newList(doc(), 'Second');
    const gone = deleteList(d, d.active);
    expect(gone.lists).toHaveLength(1);
    expect(gone.active).toBe('l1');
    expect(deleteList(gone, 'l1')).toBe(gone);
  });
  it('switches by id, finds by name or id, and ignores an unknown id', () => {
    const d = newList(doc(), 'Second');
    expect(setActive(d, 'l1').active).toBe('l1');
    expect(setActive(d, 'zzz')).toBe(d);
    expect(findList(d, 'second')?.id).toBe('l2');
    expect(findList(d, 'L1')?.name).toBe('Main');
    expect(findList(d, 'nope')).toBeUndefined();
  });
  it('columns are kept in order without repeats and are never empty', () => {
    const d = setColumns(doc(), ['volume', 'last', 'volume']);
    expect(activeList(d).columns).toEqual(['volume', 'last']);
    expect(setColumns(d, [])).toBe(d);
  });
});

describe('import and export', () => {
  const all = (): boolean => true;
  it('exports sections as ### lines and symbols with the NSE prefix', () => {
    expect(exportText(activeList(doc()))).toBe('###Indices\nNSE:NIFTY 50,NSE:NIFTY BANK\n###Stocks\nNSE:SBIN,NSE:TCS,NSE:INFY');
  });
  it('an export read back into an empty list gives the same list', () => {
    const text = exportText(activeList(doc()));
    const fresh = newList(doc(), 'Copy');
    const { doc: back, added, skipped } = importText(fresh, text, all);
    expect(added).toBe(5);
    expect(skipped).toEqual([]);
    expect(items(back)).toEqual(items(doc()));
  });
  it('reads plain symbols, any exchange prefix, repeats, and skips unknown ones with their names', () => {
    const fresh = newList(doc(), 'Plain');
    const { doc: back, added, skipped } = importText(fresh, 'sbin, nse:tcs\nbse:infy,SBIN,foo\n', (s) => s !== 'FOO');
    expect(items(back)).toEqual(['SBIN', 'TCS', 'INFY']);
    expect(added).toBe(3);
    expect(skipped).toEqual(['FOO']);
  });
  it('puts symbols under their ### heading in a list that already has it', () => {
    const { doc: back } = importText(doc(), '###Stocks\nNSE:HDFCBANK\n###New\nNSE:LT', all);
    expect(items(back)).toEqual(['#Indices', 'NIFTY 50', 'NIFTY BANK', '#Stocks', 'SBIN', 'TCS', 'INFY', 'HDFCBANK', '#New', 'LT']);
  });
});

describe('advanced columns', () => {
  it('the fundamentals and return columns are accepted, kept in order, and survive a round trip', () => {
    const d = setColumns(doc(), ['last', 'chgpct', 'mcap', 'pe', 'roe', 'r1y', 'sector']);
    expect(activeList(d).columns).toEqual(['last', 'chgpct', 'mcap', 'pe', 'roe', 'r1y', 'sector']);
    expect(validate(JSON.parse(JSON.stringify(d)))).toEqual(d);
  });

  it('the Basic and Advanced presets are valid column sets', () => {
    expect(activeList(setColumns(doc(), [...ADVANCED_COLUMNS])).columns).toEqual(ADVANCED_COLUMNS);
    expect(activeList(setColumns(doc(), [...BASIC_COLUMNS])).columns).toEqual(BASIC_COLUMNS);
    expect(COLUMN_TITLES.mcap).toContain('Mkt cap');
    for (const c of [...ADVANCED_COLUMNS, ...BASIC_COLUMNS]) expect(COLUMNS).toContain(c);
  });

  it('a made-up column is dropped when a document is read', () => {
    const d = doc();
    const odd = { ...d, lists: [{ ...d.lists[0], columns: ['last', 'ebitda'] }] };
    expect(activeList(validate(odd)).columns).toEqual(['last']);
  });

  it('sorting by a number column orders numerically and leaves blanks last in either direction', () => {
    const pe: Record<string, number | null> = { X: 25, Y: null, Z: 9 };
    let d = setColumns(with_(abc), ['pe']);
    d = cycleSort(d, 'pe'); // first click: ascending
    expect(sortedSymbols(activeList(d), (s) => pe[s])).toEqual(['Z', 'X', 'Y']);
    d = cycleSort(d, 'pe'); // second click: descending
    expect(sortedSymbols(activeList(d), (s) => pe[s])).toEqual(['X', 'Z', 'Y']);
  });
});
