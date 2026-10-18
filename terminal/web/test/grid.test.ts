import { describe, expect, it } from 'vitest';
import { defaultGrid, GridSync, indexAtMoment, LAYOUT_IDS, LAYOUTS, MAX_CELLS, momentOf, parseGridState, periodIndexes, serializeGrid, type CellPort } from '../src/chartlab/grid';

const IST = 19_800;
const utc = (y: number, m: number, d: number, hh = 0, mm = 0): number => Date.UTC(y, m - 1, d, hh, mm) / 1000;
const ist = (y: number, m: number, d: number, hh: number, mm: number): number => utc(y, m, d, hh, mm) - IST;
const daily = (dates: [number, number, number][]) => dates.map(([y, m, d]) => ({ t: utc(y, m, d) }));
/** 15-minute bars for the given days, 09:15 to 15:15. */
const quarter = (days: [number, number, number][]) => days.flatMap(([y, m, d]) => Array.from({ length: 25 }, (_, i) => ({ t: ist(y, m, d, 9, 15) + i * 900 })));

describe('layouts', () => {
  it('each shows between two and four cells, and has an area for every one', () => {
    for (const id of LAYOUT_IDS) {
      const l = LAYOUTS[id];
      expect(l.cells).toBeGreaterThanOrEqual(2);
      expect(l.cells).toBeLessThanOrEqual(MAX_CELLS);
      for (let i = 0; i < l.cells; i++) expect(l.areas).toContain(`c${i}`);
      expect(l.areas).not.toContain(`c${l.cells}`);
    }
  });
});

describe('what is kept', () => {
  it('starts as 2 by 2 with symbol and crosshair synced, at 1D, 1W, 1h and 15m', () => {
    const g = defaultGrid();
    expect(g.layout).toBe('2x2');
    expect(g.sync).toEqual({ symbol: true, interval: false, crosshair: true, time: false });
    expect(g.cells.map((c) => c.interval)).toEqual(['1D', '1W', '1h', '15m']);
    expect(g.cells.every((c) => c.symbol === null)).toBe(true);
  });

  it('comes back as it was saved', () => {
    const g = defaultGrid();
    g.layout = '1+2';
    g.sync = { symbol: false, interval: true, crosshair: false, time: true };
    g.cells[0] = { symbol: 'SBIN', interval: '7m' };
    g.cells[2] = { symbol: 'TCS', interval: '3D' };
    expect(parseGridState(serializeGrid(g))).toEqual(g);
  });

  it('uses the defaults for nothing, damage and foreign values, and never throws', () => {
    for (const text of [null, '', 'not json', '[]', 'null', '42', '{"layout":"9x9"}']) expect(parseGridState(text).layout).toBe('2x2');
    expect(parseGridState('{"layout":"9x9","sync":{"symbol":"yes"}}')).toEqual(defaultGrid());
    expect(parseGridState('{"cells":[{"symbol":"<script>","interval":"7q"},5,null]}')).toEqual(defaultGrid());
  });

  it('keeps what is usable of a partly usable value', () => {
    const g = parseGridState('{"layout":"3h","sync":{"time":true,"bogus":1},"cells":[{"symbol":"sbin","interval":"60m"},{"symbol":"x","interval":"9z"}]}');
    expect(g.layout).toBe('3h');
    expect(g.sync.time).toBe(true);
    expect(g.sync.symbol).toBe(true);
    expect(g.cells[0]).toEqual({ symbol: 'SBIN', interval: '1h' }); // upper case, one spelling
    expect(g.cells[1]).toEqual({ symbol: 'X', interval: '1W' }); // the bad interval kept the default
    expect(g.cells).toHaveLength(MAX_CELLS);
  });

  it('ignores cells past the fourth', () => {
    const many = { cells: Array.from({ length: 9 }, () => ({ symbol: 'ABC', interval: '5m' })) };
    expect(parseGridState(JSON.stringify(many)).cells).toHaveLength(MAX_CELLS);
  });
});

describe('the moment a bar stands for', () => {
  it('is its start for an intraday bar and the close of its first day for the others', () => {
    const t = ist(2026, 10, 14, 10, 30);
    expect(momentOf(t, '15m')).toBe(t);
    expect(momentOf(t, '7m')).toBe(t);
    expect(momentOf(t, '2h')).toBe(t);
    expect(momentOf(utc(2026, 10, 14), '1D')).toBe(ist(2026, 10, 14, 15, 29));
    expect(momentOf(utc(2026, 10, 12), '1W')).toBe(ist(2026, 10, 12, 15, 29));
    expect(momentOf(utc(2026, 10, 1), '3M')).toBe(ist(2026, 10, 1, 15, 29));
  });
});

describe('the bar a moment falls in', () => {
  const days = daily([[2026, 10, 12], [2026, 10, 13], [2026, 10, 14], [2026, 10, 16]]); // the 15th is a holiday
  const bars15 = quarter([[2026, 10, 13], [2026, 10, 14]]);

  it('finds an intraday bar from an intraday moment, from the same chart and from another interval', () => {
    const m = ist(2026, 10, 14, 10, 31);
    const i = indexAtMoment(bars15, m, '15m')!;
    expect(bars15[i].t).toBe(ist(2026, 10, 14, 10, 30));
    expect(indexAtMoment(bars15, ist(2026, 10, 14, 10, 30), '15m')).toBe(i);
    expect(bars15[indexAtMoment(bars15, ist(2026, 10, 14, 15, 29), '15m')!].t).toBe(ist(2026, 10, 14, 15, 15)); // the close of the day
  });

  it('finds the daily bar of an intraday moment, and the last intraday bar of a day moment', () => {
    expect(days[indexAtMoment(days, ist(2026, 10, 14, 10, 31), '1D')!].t).toBe(utc(2026, 10, 14));
    expect(days[indexAtMoment(days, ist(2026, 10, 14, 9, 15), '1D')!].t).toBe(utc(2026, 10, 14)); // the first minute of the date, not the day before
    const dayMoment = momentOf(utc(2026, 10, 14), '1D');
    expect(bars15[indexAtMoment(bars15, dayMoment, '15m')!].t).toBe(ist(2026, 10, 14, 15, 15));
  });

  it('shows nothing before the first bar', () => {
    expect(indexAtMoment(days, utc(2026, 10, 9), '1D')).toBeNull();
    expect(indexAtMoment(bars15, ist(2026, 10, 12, 10, 0), '15m')).toBeNull();
  });

  it('shows nothing after the last bar has ended, and the last bar while it runs', () => {
    expect(indexAtMoment(bars15, ist(2026, 10, 15, 10, 0), '15m')).toBeNull();
    expect(indexAtMoment(days, utc(2026, 10, 16, 5), '1D')).toBe(3);
    expect(indexAtMoment(days, utc(2026, 11, 2), '1D')).toBeNull();
  });

  it('shows nothing for an intraday gap between sessions, and the earlier bar for a holiday between daily bars', () => {
    expect(indexAtMoment(bars15, ist(2026, 10, 13, 16, 30), '15m')).toBeNull(); // after the close, before the next open
    expect(indexAtMoment(days, ist(2026, 10, 15, 11, 0), '1D')).toBe(2); // the holiday: the bar before it, as it was the last price
  });

  it('lets a weekly or monthly bar cover the days it holds', () => {
    const weeks = [{ t: utc(2026, 10, 5) }, { t: utc(2026, 10, 12) }];
    expect(indexAtMoment(weeks, ist(2026, 10, 7, 11, 0), '1W')).toBe(0);
    expect(indexAtMoment(weeks, ist(2026, 10, 14, 11, 0), '1W')).toBe(1);
    expect(indexAtMoment(weeks, ist(2026, 10, 20, 11, 0), '1W')).toBeNull(); // more than a week after the last bar began
    const months = [{ t: utc(2026, 9, 1) }, { t: utc(2026, 10, 1) }];
    expect(indexAtMoment(months, ist(2026, 10, 31, 11, 0), '1M')).toBe(1);
  });

  it('says nothing for no bars or a moment that is not a number', () => {
    expect(indexAtMoment([], 1, '1D')).toBeNull();
    expect(indexAtMoment(days, Number.NaN, '1D')).toBeNull();
  });
});

describe('a period', () => {
  const days = daily([[2026, 10, 12], [2026, 10, 13], [2026, 10, 14], [2026, 10, 16]]);
  const bars15 = quarter([[2026, 10, 13], [2026, 10, 14]]);

  it('shows the bars from the first moment to the last', () => {
    expect(periodIndexes(days, ist(2026, 10, 13, 10, 0), ist(2026, 10, 14, 10, 0), '1D')).toEqual({ from: 1, to: 2 });
    expect(periodIndexes(bars15, ist(2026, 10, 13, 10, 0), ist(2026, 10, 13, 11, 0), '15m')).toEqual({ from: 3, to: 7 });
  });

  it('starts at the first bar when it begins earlier, and ends at the last when it ends later', () => {
    expect(periodIndexes(days, utc(2026, 9, 1), utc(2026, 11, 30), '1D')).toEqual({ from: 0, to: 3 });
  });

  it('starts with the next bar when it begins in a gap', () => {
    const r = periodIndexes(bars15, ist(2026, 10, 13, 18, 0), ist(2026, 10, 14, 10, 0), '15m')!;
    expect(bars15[r.from].t).toBe(ist(2026, 10, 14, 9, 15));
  });

  it('is nothing when it is wholly before the bars or after them', () => {
    expect(periodIndexes(days, utc(2026, 9, 1), utc(2026, 9, 30), '1D')).toBeNull();
    expect(periodIndexes(bars15, ist(2026, 10, 20, 9, 0), ist(2026, 10, 21, 9, 0), '15m')).toBeNull();
    expect(periodIndexes(days, 5, 1, '1D')).toBeNull();
    expect(periodIndexes([], 1, 2, '1D')).toBeNull();
  });
});

describe('sending between cells', () => {
  function cell(name: string, log: string[]): CellPort {
    return {
      applySymbol: (s) => log.push(`${name} symbol ${s}`),
      applyInterval: (i) => log.push(`${name} interval ${i}`),
      showMoment: (m) => log.push(`${name} moment ${m}`),
      showPeriod: (a, b) => log.push(`${name} period ${a}-${b}`),
      followReplay: (m) => log.push(`${name} replay ${m}`),
    };
  }
  function grid(flags?: Partial<GridSync['flags']>) {
    const log: string[] = [];
    const sync = new GridSync();
    sync.flags = { symbol: false, interval: false, crosshair: false, time: false, ...flags };
    const cells = ['a', 'b', 'c'].map((n) => cell(n, log));
    sync.setCells(cells);
    return { sync, cells, log };
  }

  it('sends nothing while every switch is off', () => {
    const { sync, cells, log } = grid();
    sync.symbolPicked(cells[0], 'SBIN');
    sync.intervalPicked(cells[0], '5m');
    sync.crosshair(cells[0], 10);
    sync.period(cells[0], 1, 2);
    expect(log).toEqual([]);
  });

  it('sends each thing to the others only, never back to the cell it came from, and only for its own switch', () => {
    const { sync, cells, log } = grid({ symbol: true });
    sync.symbolPicked(cells[1], 'TCS');
    sync.intervalPicked(cells[1], '5m');
    expect(log).toEqual(['a symbol TCS', 'c symbol TCS']);

    log.length = 0;
    sync.flags = { symbol: false, interval: true, crosshair: false, time: false };
    sync.intervalPicked(cells[0], '7m');
    expect(log).toEqual(['b interval 7m', 'c interval 7m']);

    log.length = 0;
    sync.flags = { symbol: false, interval: false, crosshair: true, time: false };
    sync.crosshair(cells[2], 99);
    sync.crosshair(cells[2], null);
    expect(log).toEqual(['a moment 99', 'b moment 99', 'a moment null', 'b moment null']);

    log.length = 0;
    sync.flags = { symbol: false, interval: false, crosshair: false, time: true };
    sync.period(cells[0], 5, 9);
    expect(log).toEqual(['b period 5-9', 'c period 5-9']);
  });

  it('tells every other cell where a replay has got to, and when it ends, with no switch', () => {
    const { sync, cells, log } = grid();
    sync.replay(cells[1], 500);
    sync.replay(cells[1], null);
    expect(log).toEqual(['a replay 500', 'c replay 500', 'a replay null', 'c replay null']);
  });

  it('a cell that is not registered any more is not told', () => {
    const { sync, cells, log } = grid({ symbol: true });
    sync.setCells([cells[0], cells[1]]);
    sync.symbolPicked(cells[0], 'X');
    expect(log).toEqual(['b symbol X']);
  });
});
