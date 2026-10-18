import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { kagi, pointAndFigure, sequence } from '../src/chartlab/derived';

const closes = (...cs: number[]): Bar[] => cs.map((c, i) => ({ t: 1000 + i, o: c, h: c, l: c, c, v: 10 }));
const lines = (bars: Bar[], reversal: number) => kagi(bars, reversal).map((k) => ({ o: k.o, c: k.c, thick: k.thick, flip: k.flip }));

describe('Kagi', () => {
  it('draws nothing until the close has moved the reversal amount from the first one', () => {
    expect(kagi(closes(10, 11, 10.5), 2)).toEqual([]);
    expect(kagi(closes(10), 2)).toEqual([]);
    expect(kagi([], 2)).toEqual([]);
    expect(kagi(closes(10, 12, 14), 0)).toEqual([]); // no reversal amount, no chart
  });

  it('extends a line in its direction and turns only on a full reversal, starting where the last line ended', () => {
    const got = lines(closes(10, 11, 12, 11, 9.5, 10, 13), 2);
    expect(got.map((k) => [k.o, k.c])).toEqual([[10, 12], [12, 9.5], [9.5, 13]]);
  });

  it('a one-box wobble against the line changes nothing', () => {
    expect(lines(closes(10, 12, 11.5, 12.5, 11.2), 2).map((k) => [k.o, k.c])).toEqual([[10, 12.5]]);
  });

  it('turns thick above the previous swing high and thin below the previous swing low, at the price where it changes', () => {
    const got = lines(closes(10, 11, 12, 11, 9.5, 10, 13, 14, 12, 8, 9, 10, 15), 2);
    expect(got.map((k) => [k.o, k.c, k.thick, k.flip])).toEqual([
      [10, 12, true, undefined], // a first line up begins thick
      [12, 9.5, true, undefined], // there is no earlier low yet to fall through
      [9.5, 14, true, undefined],
      [14, 8, false, 9.5], // falls through the earlier low of 9.5: thin from there
      [8, 15, true, 14], // rises through the earlier high of 14: thick from there
    ]);
  });

  it('gives each line the stamp of the source bar that last moved it, its extremes and the volume it saw', () => {
    const k = kagi(closes(10, 12, 13, 10), 2);
    expect(k.map((x) => [x.t, x.h, x.l, x.v])).toEqual([[1002, 13, 10, 20], [1003, 13, 10, 10]]);
  });

  it('survives sequence(), which restamps the lines and keeps their extra fields', () => {
    const out = sequence(kagi(closes(10, 12, 9), 2), 5000, 60);
    expect(out.map((x) => x.t)).toEqual([5000, 5060]);
    expect(out[0].thick).toBe(true);
  });
});

describe('Point and figure', () => {
  const columns = (bars: Bar[], box = 1) => pointAndFigure(bars, box, 3).map((c) => ({ l: c.l, h: c.h, up: c.up }));

  it('draws nothing for a move of less than one box, or without a box size', () => {
    expect(columns(closes(10.2, 10.8, 10.4))).toEqual([]);
    expect(pointAndFigure(closes(10, 12), 0, 3)).toEqual([]);
    expect(columns([])).toEqual([]);
  });

  it('builds a rising column, turns to a falling one after three boxes back, and back up after three', () => {
    expect(columns(closes(10.2, 10.8, 11.1, 12.5, 12.0, 9.9, 9.0, 12.4, 13.2))).toEqual([
      { l: 10, h: 13, up: true }, // X from box 10 to box 12
      { l: 9, h: 12, up: false }, // O starts one box under the top X (11) and goes to 9
      { l: 10, h: 14, up: true }, // X starts one box over the bottom O (10) and goes to 13
    ]);
  });

  it('a pull back of less than three boxes leaves the column as it was', () => {
    expect(columns(closes(10.5, 11.2, 9.8))).toEqual([{ l: 10, h: 12, up: true }]);
  });

  it('puts every box edge on the grid whatever the box size, and carries the box size on each column', () => {
    const cols = pointAndFigure(closes(100, 104, 109, 118, 96), 5, 3);
    expect(cols.map((c) => [c.l, c.h, c.box])).toEqual([[100, 120, 5], [95, 115, 5]]);
    expect(cols.every((c) => (c.l / 5) % 1 === 0 && (c.h / 5) % 1 === 0)).toBe(true);
  });

  it('opens a column at the bottom for X and the top for O, so open and close read as direction', () => {
    const [x, o] = pointAndFigure(closes(10.2, 11.1, 12.5, 9.9), 1, 3);
    expect([x.o, x.c, o.o, o.c]).toEqual([10, 13, 12, 9]);
  });
});
