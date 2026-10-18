import { describe, expect, it } from 'vitest';
import { layout, type Item, type Tile } from '../src/treemap';

const area = (t: { w: number; h: number }): number => t.w * t.h;
const inside = (t: Tile, w: number, h: number): boolean => t.x >= -1e-6 && t.y >= -1e-6 && t.x + t.w <= w + 1e-6 && t.y + t.h <= h + 1e-6;
const overlap = (a: Tile, b: Tile): number => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
const aspect = (t: Tile): number => Math.max(t.w / t.h, t.h / t.w);

const items = (...values: number[]): Item[] => values.map((v, i) => ({ id: `I${i}`, value: v }));

describe('an ungrouped treemap', () => {
  it('gives each item an area in proportion to its value and fills the box', () => {
    const { tiles } = layout(items(6, 6, 4, 3, 2, 2, 1), 600, 400);
    const values: Record<string, number> = { I0: 6, I1: 6, I2: 4, I3: 3, I4: 2, I5: 2, I6: 1 };
    for (const t of tiles) expect(area(t) / (600 * 400)).toBeCloseTo(values[t.id] / 24, 6);
    expect(tiles.reduce((s, t) => s + area(t), 0)).toBeCloseTo(600 * 400, 4);
  });

  it('keeps every tile inside the box and no two tiles on top of each other', () => {
    const { tiles } = layout(items(50, 30, 20, 15, 12, 10, 8, 5, 5, 3, 2, 1), 800, 500);
    expect(tiles).toHaveLength(12);
    for (const t of tiles) expect(inside(t, 800, 500)).toBe(true);
    for (let i = 0; i < tiles.length; i++) for (let j = i + 1; j < tiles.length; j++) expect(overlap(tiles[i], tiles[j])).toBeLessThan(1e-6);
  });

  it('keeps tiles near square: the worst aspect ratio of the classic example stays small', () => {
    const { tiles } = layout(items(6, 6, 4, 3, 2, 2, 1), 600, 400);
    expect(Math.max(...tiles.map(aspect))).toBeLessThan(3);
  });

  it('is the same every time, and a tie in value keeps the order the items came in', () => {
    const a = layout(items(5, 5, 5, 5), 400, 400);
    const b = layout(items(5, 5, 5, 5), 400, 400);
    expect(a).toEqual(b);
    expect(a.tiles.map((t) => t.id)).toEqual(['I0', 'I1', 'I2', 'I3']);
  });

  it('copes with one item, nothing, and values that cannot be drawn', () => {
    const one = layout(items(7), 300, 200).tiles;
    expect(one).toHaveLength(1);
    expect(area(one[0])).toBeCloseTo(300 * 200);
    expect(layout([], 300, 200).tiles).toEqual([]);
    expect(layout(items(0, -3, NaN, Infinity), 300, 200).tiles).toEqual([]);
    expect(layout(items(4, 0, 6), 300, 200).tiles.map((t) => t.id).sort()).toEqual(['I0', 'I2']);
    expect(layout(items(1, 1), 0, 200).tiles).toEqual([]);
  });
});

describe('a grouped treemap', () => {
  const grouped: Item[] = [
    { id: 'A1', value: 40, group: 'Alpha' }, { id: 'A2', value: 20, group: 'Alpha' }, { id: 'A3', value: 10, group: 'Alpha' },
    { id: 'B1', value: 25, group: 'Beta' }, { id: 'B2', value: 5, group: 'Beta' },
  ];

  it('boxes each group with its tiles inside, group boxes in proportion to their totals', () => {
    const { tiles, groups } = layout(grouped, 800, 500, { labelHeight: 0, gap: 0 });
    expect(groups.map((g) => g.name).sort()).toEqual(['Alpha', 'Beta']);
    const alpha = groups.find((g) => g.name === 'Alpha')!;
    const beta = groups.find((g) => g.name === 'Beta')!;
    expect(area(alpha) / area(beta)).toBeCloseTo(70 / 30, 5);
    for (const t of tiles) {
      const box = groups.find((g) => g.name === t.group)!;
      expect(t.x).toBeGreaterThanOrEqual(box.x - 1e-6);
      expect(t.y).toBeGreaterThanOrEqual(box.y - 1e-6);
      expect(t.x + t.w).toBeLessThanOrEqual(box.x + box.w + 1e-6);
      expect(t.y + t.h).toBeLessThanOrEqual(box.y + box.h + 1e-6);
    }
    expect(overlap(alpha as unknown as Tile, beta as unknown as Tile)).toBeLessThan(1e-6);
  });

  it('leaves room for the group label above the tiles and a gap around them', () => {
    const { tiles, groups } = layout(grouped, 800, 500, { labelHeight: 18, gap: 2 });
    for (const t of tiles) {
      const box = groups.find((g) => g.name === t.group)!;
      expect(t.y).toBeGreaterThanOrEqual(box.y + 18 - 1e-6);
      expect(t.x).toBeGreaterThanOrEqual(box.x + 2 - 1e-6);
      expect(t.x + t.w).toBeLessThanOrEqual(box.x + box.w - 2 + 1e-6);
    }
    expect(groups.find((g) => g.name === 'Alpha')!.total).toBe(70);
  });

  it('orders groups largest first and tiles largest first within a group', () => {
    const { groups, tiles } = layout(grouped, 800, 500);
    expect(groups[0].name).toBe('Alpha');
    expect(tiles.filter((t) => t.group === 'Alpha').map((t) => t.id)).toEqual(['A1', 'A2', 'A3']);
  });

  it('draws a group too small for a label without one rather than with a label that does not fit', () => {
    const many: Item[] = [{ id: 'big', value: 1000, group: 'Big' }, { id: 'tiny', value: 1, group: 'Tiny' }];
    const { groups, tiles } = layout(many, 400, 300, { labelHeight: 18, gap: 2 });
    const tiny = groups.find((g) => g.name === 'Tiny')!;
    expect(tiny.labelled).toBe(false);
    expect(groups.find((g) => g.name === 'Big')!.labelled).toBe(true);
    expect(tiles.find((t) => t.id === 'tiny')).toBeDefined();
  });

  it('items with no group are one unnamed group', () => {
    const { groups, tiles } = layout([{ id: 'x', value: 3 }, { id: 'y', value: 1 }], 400, 300);
    expect(tiles).toHaveLength(2);
    expect(groups).toEqual([]); // nothing to box
  });
});
