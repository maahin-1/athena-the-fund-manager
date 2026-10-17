import { describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import { ChartModel, heikinAshi } from '../src/chartlab/model';
import { defaults, find } from '../src/chartlab/registry';
import vectors from './fixtures/indicator_vectors.json';

const IST = 19_800;
/** 2026-10-14 (a Wednesday) 09:15 IST as unix seconds. */
const OPEN_0915 = Date.UTC(2026, 9, 14, 9, 15) / 1000 - IST;

function daily(n = 60): Bar[] {
  const today = Math.floor((OPEN_0915 + IST) / 86_400) * 86_400; // a daily bar is stamped with midnight UTC of its trading date, as barTime does
  return Array.from({ length: n }, (_, i) => ({ t: today - (n - 1 - i) * 86_400, o: 100 + i, h: 103 + i, l: 99 + i, c: 101 + i, v: 1000 }));
}

function minute(): Bar[] {
  return [{ t: Math.floor(OPEN_0915 / 60) * 60, o: 100, h: 101, l: 99, c: 100.5, v: 500 }];
}

describe('live ticks (the edge rules, against a fake feed)', () => {
  it('a tick inside the last minute updates its close, high, low and volume', () => {
    const m = new ChartModel();
    m.setBars(minute(), '1m');
    const t = OPEN_0915 + 30;
    expect(m.tick(102, t, 10)).toBe('updated');
    expect(m.tick(98.5, t + 5, 5)).toBe('updated');
    expect(m.bars).toHaveLength(1);
    expect(m.bars[0]).toMatchObject({ o: 100, h: 102, l: 98.5, c: 98.5, v: 515 });
  });

  it('a tick in a later minute appends a new bar opening at its price', () => {
    const m = new ChartModel();
    m.setBars(minute(), '1m');
    expect(m.tick(100.8, OPEN_0915 + 61)).toBe('appended');
    expect(m.bars).toHaveLength(2);
    expect(m.bars[1]).toMatchObject({ o: 100.8, h: 100.8, l: 100.8, c: 100.8 });
  });

  it('ignores a tick after the close, on a weekend, far from the price, for an earlier bar, or with no price', () => {
    const m = new ChartModel();
    m.setBars(minute(), '1m');
    const afterClose = Date.UTC(2026, 9, 14, 15, 31) / 1000 - IST;
    const saturday = Date.UTC(2026, 9, 17, 11, 0) / 1000 - IST;
    expect(m.tick(100, afterClose)).toBe('ignored');
    expect(m.tick(100, saturday)).toBe('ignored');
    expect(m.tick(140, OPEN_0915 + 10)).toBe('ignored'); // more than 25 percent away
    expect(m.tick(0, OPEN_0915 + 10)).toBe('ignored');
    expect(m.tick(101, OPEN_0915 - 120)).toBe('ignored'); // before the session
    expect(m.bars).toHaveLength(1);
    expect(m.bars[0].c).toBe(100.5);
  });

  it('a daily chart takes a tick any time of day into the day bar, and a chart with no bars takes none', () => {
    const m = new ChartModel();
    expect(m.tick(100, OPEN_0915)).toBe('ignored');
    m.setBars(daily(), '1D'); // the last bar is today's
    const before = m.bars[m.bars.length - 1].c;
    expect(m.tick(before + 1, Date.UTC(2026, 9, 14, 17, 0) / 1000)).toBe('updated');
    expect(m.bars[m.bars.length - 1].c).toBe(before + 1);
  });

  it('indicators are recomputed by the same tick: the last value equals a fresh computation', () => {
    const m = new ChartModel();
    const bars = vectors.bars.slice(0, 100) as Bar[];
    m.setBars(bars.map((b, i) => ({ ...b, t: Math.floor(OPEN_0915 / 60) * 60 + i * 60 - 99 * 60 })), '1m');
    const uid = m.addIndicator('rsi')!;
    const before = m.series(uid).rsi[99];
    const last = m.bars[99];
    m.tick(last.c * 1.01, last.t + 5);
    const after = m.series(uid).rsi[99];
    expect(after).not.toBe(before);
    expect(after).toBe(find('rsi')!.compute(m.bars, defaults(find('rsi')!)).rsi[99]);
  });

  it('tells its listeners once per change', () => {
    const m = new ChartModel();
    m.setBars(minute(), '1m');
    const seen = vi.fn();
    m.subscribe(seen);
    m.tick(101, OPEN_0915 + 5);
    m.tick(140, OPEN_0915 + 6); // ignored: no repaint
    expect(seen).toHaveBeenCalledTimes(1);
  });
});

describe('indicators on the model', () => {
  it('adds with defaults, rejects an unknown id, clamps a changed setting and explains a bad one', () => {
    const m = new ChartModel();
    m.setBars(daily(), '1D');
    expect(m.addIndicator('nope')).toBeNull();
    const uid = m.addIndicator('ema', { length: 9 })!;
    expect(m.indicators[0].params).toEqual({ length: 9 });
    expect(m.updateIndicator(uid, { length: 100000 })).toBeNull();
    expect(m.indicators[0].params).toEqual({ length: 500 });
    expect(m.updateIndicator(uid, { length: 'abc' })).toMatch(/number/);
    expect(m.indicators[0].params).toEqual({ length: 500 });
    expect(m.updateIndicator('missing', {})).toBe('no such indicator');
  });

  it('hides and removes', () => {
    const m = new ChartModel();
    m.setBars(daily(), '1D');
    const a = m.addIndicator('sma')!;
    const b = m.addIndicator('rsi')!;
    m.hideIndicator(a, true);
    expect(m.window(59)!.values[a]).toBeUndefined(); // a hidden indicator is not in the data window
    expect(m.window(59)!.values[b].rsi).not.toBeNull();
    m.removeIndicator(a);
    expect(m.indicators.map((i) => i.uid)).toEqual([b]);
  });
});

describe('chart types and compare', () => {
  it('Heikin Ashi follows the textbook on a hand vector', () => {
    const ha = heikinAshi([
      { t: 1, o: 10, h: 12, l: 9, c: 11, v: 1 },
      { t: 2, o: 11, h: 14, l: 10, c: 13, v: 1 },
    ]);
    expect(ha[0]).toMatchObject({ c: 10.5, o: 10.5, h: 12, l: 9 });
    expect(ha[1].c).toBe(12); // (11 + 14 + 10 + 13) / 4
    expect(ha[1].o).toBe(10.5); // (10.5 + 10.5) / 2
    expect(ha[1].h).toBe(14);
    expect(ha[1].l).toBe(10);
  });

  it('the real bars stay real when a Heikin Ashi chart is shown', () => {
    const m = new ChartModel();
    m.setBars(daily(5), '1D');
    m.setType('heikin');
    expect(m.displayBars()[1].c).not.toBe(m.bars[1].c);
    expect(m.bars[1].c).toBe(102);
    m.setType('candles');
    expect(m.displayBars()).toBe(m.bars);
  });

  it('compare starts level with the chart at the first shared bar and is null where the other symbol has no bar', () => {
    const m = new ChartModel();
    const mine = daily(5); // closes 101..105
    m.setBars(mine, '1D');
    const theirs: Bar[] = [mine[1], mine[2], mine[4]].map((b, k) => ({ ...b, c: [50, 55, 60][k] }));
    m.setCompare('OTHER', theirs);
    const s = m.compareSeries();
    expect(s[0]).toBeNull();
    expect(s[1]).toBeCloseTo(102, 10); // level with the chart's own close at the first shared bar
    expect(s[2]).toBeCloseTo(102 * (55 / 50), 10);
    expect(s[3]).toBeNull();
    expect(s[4]).toBeCloseTo(102 * (60 / 50), 10);
    m.setCompare(null);
    expect(m.compareSeries().every((v) => v === null)).toBe(true);
  });
});

describe('data window and state', () => {
  it('shows prices, change from the bar before and indicator values, null before warm-up', () => {
    const m = new ChartModel();
    m.setBars(daily(), '1D');
    const uid = m.addIndicator('sma', { length: 20 })!;
    const w = m.window(30)!;
    expect(w).toMatchObject({ c: 131, change: 1 });
    expect(w.values[uid].ma).toBeCloseTo(m.series(uid).ma[30] as number, 10);
    expect(m.window(5)!.values[uid].ma).toBeNull();
    expect(m.window(1000)).toBeNull();
  });

  it('state round-trips through restore, giving the compare symbol back to fetch', () => {
    const a = new ChartModel();
    a.setBars(daily(), '1D');
    a.setType('line');
    a.setScale('log');
    a.setAdjusted(false);
    const uid = a.addIndicator('bb', { mult: 2.5 })!;
    a.updateIndicator(uid, {}, { upper: '#ff0000' });
    a.hideIndicator(a.addIndicator('rsi')!, true);
    a.setCompare('NIFTY 50', []);
    const saved = a.state();
    const b = new ChartModel();
    b.setBars(daily(), '1D');
    expect(b.restore(saved)).toBe('NIFTY 50');
    expect(b.state()).toEqual(saved);
    const first = b.indicators[0].uid;
    b.restore(saved);
    expect(b.indicators[0].uid).not.toBe(first); // each restore makes new instances
  });
});
