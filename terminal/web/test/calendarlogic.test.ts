import { describe, expect, it } from 'vitest';
import { CATEGORIES, addDays, dayLabel, filterEntries, groupByDate, merge, rangeFor, type CalBody } from '../src/screens/calendarlogic';

const body: CalBody = {
  from: '2026-10-19', to: '2026-10-31',
  events: [
    { symbol: 'AAA', name: 'Alpha Ltd', date: '2026-10-23', purpose: 'Financial Results/Dividend', kinds: ['results', 'dividend'], description: 'To consider the results and a dividend' },
    { symbol: 'BBB', name: 'Beta Ltd', date: '2026-10-19', purpose: 'Financial Results', kinds: ['results'], description: 'To consider the results' },
    { symbol: 'CCC', name: 'Gamma Ltd', date: '2026-10-19', purpose: 'Fund Raising', kinds: ['fundraising'], description: '' },
  ],
  actions: [
    { symbol: 'BBB', name: 'Beta Ltd', date: '2026-10-19', kind: 'dividend', subject: 'Interim Dividend - Rs 5 Per Share' },
    { symbol: 'DDD', name: 'Delta Ltd', date: '2026-10-22', kind: 'split', subject: 'Face Value Split' },
    { symbol: 'EEE', name: 'Eps Ltd', date: '2026-10-22', kind: 'meeting', subject: 'Annual General Meeting' },
  ],
  events_through: '2026-11-13', actions_through: '2026-10-23', truncated: false,
};

describe('the date ranges', () => {
  it('count from today and look back for the past month', () => {
    expect(rangeFor('week', '2026-10-18')).toEqual({ from: '2026-10-18', to: '2026-10-24' });
    expect(rangeFor('fortnight', '2026-10-18')).toEqual({ from: '2026-10-18', to: '2026-10-31' });
    expect(rangeFor('month', '2026-10-18')).toEqual({ from: '2026-10-18', to: '2026-11-17' });
    expect(rangeFor('past', '2026-10-18')).toEqual({ from: '2026-09-18', to: '2026-10-18' });
  });

  it('add days across a month and a year end', () => {
    expect(addDays('2026-12-30', 3)).toBe('2027-01-02');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });

  it('write a day for people', () => {
    expect(dayLabel('2026-10-19')).toBe('Mon 19 Oct 2026');
  });
});

describe('merging meetings and actions', () => {
  const entries = merge(body);

  it('makes one entry for each, with what it is and what it says', () => {
    expect(entries).toHaveLength(6);
    const meeting = entries.find((e) => e.symbol === 'AAA')!;
    expect(meeting).toMatchObject({ source: 'meeting', date: '2026-10-23', label: 'Financial Results/Dividend', detail: 'To consider the results and a dividend' });
    const action = entries.find((e) => e.symbol === 'DDD')!;
    expect(action).toMatchObject({ source: 'action', label: 'Face Value Split', detail: 'ex-date' });
  });

  it('puts each in the categories a reader filters by', () => {
    const cats = (symbol: string, source: string) => entries.find((e) => e.symbol === symbol && e.source === source)!.cats.sort();
    expect(cats('AAA', 'meeting')).toEqual(['dividend', 'results']);
    expect(cats('CCC', 'meeting')).toEqual(['rights']); // fund raising
    expect(cats('BBB', 'action')).toEqual(['dividend']);
    expect(cats('DDD', 'action')).toEqual(['splitbonus']);
    expect(cats('EEE', 'action')).toEqual(['meeting']);
  });

  it('knows every category it can use', () => {
    const known = new Set(CATEGORIES.map((c) => c.id));
    for (const e of entries) for (const c of e.cats) expect(known.has(c)).toBe(true);
  });
});

describe('filtering', () => {
  const entries = merge(body);
  const none = { cats: new Set<string>(), text: '', symbols: null, watched: null };

  it('keeps everything when nothing is asked for', () => {
    expect(filterEntries(entries, none)).toHaveLength(6);
  });

  it('keeps the categories chosen, and an entry that is in any of them', () => {
    expect(filterEntries(entries, { ...none, cats: new Set(['results']) }).map((e) => e.symbol).sort()).toEqual(['AAA', 'BBB']);
    expect(filterEntries(entries, { ...none, cats: new Set(['dividend']) }).map((e) => e.symbol).sort()).toEqual(['AAA', 'BBB']);
    expect(filterEntries(entries, { ...none, cats: new Set(['results', 'splitbonus']) })).toHaveLength(3);
  });

  it('searches symbol, name and wording, whatever the case', () => {
    expect(filterEntries(entries, { ...none, text: 'gamma' }).map((e) => e.symbol)).toEqual(['CCC']);
    expect(filterEntries(entries, { ...none, text: 'aaa' })).toHaveLength(1);
    expect(filterEntries(entries, { ...none, text: 'split' }).map((e) => e.symbol)).toEqual(['DDD']);
  });

  it('keeps only the stocks of a set (the current screen, the watchlist) when one is given', () => {
    expect(filterEntries(entries, { ...none, symbols: new Set(['BBB', 'DDD']) })).toHaveLength(3);
    expect(filterEntries(entries, { ...none, symbols: new Set() })).toHaveLength(0);
    expect(filterEntries(entries, { ...none, watched: new Set(['AAA']) }).map((e) => e.symbol)).toEqual(['AAA']);
  });
});

describe('grouping by date', () => {
  it('groups in date order with a count of meetings and ex-dates, meetings before ex-dates within a day', () => {
    const days = groupByDate(merge(body));
    expect(days.map((d) => d.date)).toEqual(['2026-10-19', '2026-10-22', '2026-10-23']);
    expect(days[0]).toMatchObject({ meetings: 2, actions: 1, label: 'Mon 19 Oct 2026' });
    expect(days[0].entries.map((e) => `${e.source}:${e.symbol}`)).toEqual(['meeting:BBB', 'meeting:CCC', 'action:BBB']);
    expect(groupByDate([])).toEqual([]);
  });
});
