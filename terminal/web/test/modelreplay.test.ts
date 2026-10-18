import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { ChartModel } from '../src/chartlab/model';
import { barEnd } from '../src/chartlab/replay';

const utc = (y: number, m: number, d: number, hh = 0, mm = 0): number => Date.UTC(y, m - 1, d, hh, mm) / 1000;
const IST = 19_800;

/** Sixty weekdays of daily bars from Monday 3 August 2026, closes rising with a wobble. */
function daily(): Bar[] {
  const out: Bar[] = [];
  for (let d = 0; out.length < 60; d++) {
    const t = utc(2026, 8, 3) + d * 86_400;
    const dow = new Date(t * 1000).getUTCDay();
    if (dow === 0 || dow === 6) continue;
    const c = 100 + out.length + Math.sin(out.length) * 3;
    out.push({ t, o: c - 1, h: c + 2, l: c - 2, c, v: 1000 + out.length });
  }
  return out;
}

function model(bars = daily()) {
  const m = new ChartModel();
  m.setBars(bars, '1D');
  const kinds: string[] = [];
  m.subscribe((k) => kinds.push(k));
  return { m, bars, kinds };
}

describe('replay in the model', () => {
  it('shows only the bars that have ended by the moment, as a prefix of the loaded bars, and leaves the loaded bars alone', () => {
    const { m, bars } = model();
    m.showAt(barEnd(bars, 19, '1D'));
    expect(m.replaying).toBe(true);
    expect(m.bars).toHaveLength(20);
    expect(m.bars[19]).toBe(bars[19]);
    expect(m.loadedBars).toBe(bars);
    expect(bars).toHaveLength(60);
    expect(m.replayMoment).toBe(barEnd(bars, 19, '1D'));
  });

  it('adding one bar is a tick for the chart, going back or jumping is a rebuild', () => {
    const { m, bars, kinds } = model();
    m.showAt(barEnd(bars, 19, '1D'));
    kinds.length = 0;
    m.showAt(barEnd(bars, 20, '1D'));
    expect(kinds).toEqual(['tick']);
    m.showAt(barEnd(bars, 19, '1D'));
    m.showAt(barEnd(bars, 30, '1D'));
    m.showAt(barEnd(bars, 30, '1D'));
    expect(kinds).toEqual(['tick', 'structure', 'structure', 'structure']);
  });

  it('computes indicators on the revealed bars only: the same numbers as on the bars cut there', () => {
    const { m, bars } = model();
    const uid = m.addIndicator('sma', { length: 5 })!;
    m.showAt(barEnd(bars, 29, '1D'));
    const during = m.series(uid);
    const cut = new ChartModel();
    cut.setBars(bars.slice(0, 30), '1D');
    const cutUid = cut.addIndicator('sma', { length: 5 })!;
    expect(during).toEqual(cut.series(cutUid));
    expect(Object.values(during)[0]).toHaveLength(30);
  });

  it('shows the data window of the last revealed bar', () => {
    const { m, bars } = model();
    m.showAt(barEnd(bars, 9, '1D'));
    expect(m.window(m.bars.length - 1)?.c).toBe(bars[9].c);
    expect(m.window(10)).toBeNull(); // a bar not yet revealed has no values
  });

  it('ignores live prices while replaying, and takes them again after', () => {
    const { m, bars } = model();
    const last = bars[bars.length - 1];
    m.showAt(barEnd(bars, 19, '1D'));
    const t = last.t + 3600;
    expect(m.tick(last.c + 1, t)).toBe('ignored');
    expect(m.bars).toHaveLength(20);
    m.endReplay();
    expect(m.replaying).toBe(false);
    expect(m.bars).toBe(bars);
    expect(m.replayMoment).toBeNull();
    // back in the live chart a price inside the last bar's day updates it
    expect(['updated', 'ignored']).toContain(m.tick(last.c + 0.5, last.t + 5 * 3600));
  });

  it('cuts the compare line at the revealed bars', () => {
    const { m, bars } = model();
    m.setCompare('NIFTY 50', bars.map((b) => ({ ...b, c: b.c * 2 })));
    m.showAt(barEnd(bars, 14, '1D'));
    const line = m.compareSeries();
    expect(line).toHaveLength(15);
    expect(line.every((v) => v !== null)).toBe(true);
  });

  it('draws price-move chart types from the revealed bars', () => {
    const { m, bars } = model();
    m.setType('linebreak');
    const full = m.displayBars().length;
    m.showAt(barEnd(bars, 19, '1D'));
    expect(m.displayBars().length).toBeLessThan(full);
    expect(m.displayBars().length).toBeGreaterThan(0);
  });

  it('keeps the moment when other bars arrive (another interval, adjusted or raw prices)', () => {
    const { m, bars } = model();
    const moment = barEnd(bars, 19, '1D');
    m.showAt(moment);
    const again = daily().map((b) => ({ ...b, c: b.c + 1 })); // the same days with other prices
    m.setBars(again, '1D');
    expect(m.replaying).toBe(true);
    expect(m.bars).toHaveLength(20);
    expect(m.bars[19].c).toBe(again[19].c);
    expect(m.loadedBars).toBe(again);
  });

  it('shows a coarser chart only the bars that have ended by the moment of a finer one', () => {
    const days = daily();
    const weeks: Bar[] = [];
    for (let i = 0; i < days.length; i += 5) weeks.push({ ...days[i], h: 200 }); // a week starts on the first of each five weekdays
    const m = new ChartModel();
    m.setBars(weeks, '1W');
    m.showAt(utc(2026, 8, 12, 10, 0) - IST); // Wednesday of the second week
    expect(m.bars).toHaveLength(1); // the first week is complete; the second is running
    m.showAt(utc(2026, 8, 3, 0) - 1);
    expect(m.bars).toHaveLength(0); // before any week has ended
    expect(m.window(0)).toBeNull();
  });

  it('ending replay when it never began changes nothing', () => {
    const { m, bars, kinds } = model();
    m.endReplay();
    expect(kinds).toEqual([]);
    expect(m.bars).toBe(bars);
  });

  it('starting again after ending begins from the full bars', () => {
    const { m, bars } = model();
    m.showAt(barEnd(bars, 9, '1D'));
    m.endReplay();
    m.showAt(barEnd(bars, 24, '1D'));
    expect(m.bars).toHaveLength(25);
    expect(m.loadedBars).toBe(bars);
  });
});
