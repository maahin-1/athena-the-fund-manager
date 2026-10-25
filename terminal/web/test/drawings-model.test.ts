import { describe, expect, it } from 'vitest';
import {
  add, duplicate, DrawingError, GROUPS, MAX_DRAWINGS, MAX_TEXT, moveBy, moveHandle, newDrawing, remove, removeAll, reorder, serializeDoc, setAllHidden, setAllLocked, TOOL_IDS, TOOLS,
  update, validateDoc, type Drawing,
} from '../src/chartlab/drawings/model';

const a = (t: number, p: number) => ({ t, p });
const trend = (): Drawing => newDrawing('trend', [a(100, 10), a(200, 20)]);
const doc = (drawings: Drawing[]) => JSON.parse(serializeDoc('SBIN', drawings));

describe('tools', () => {
  it('there are 50 tools, each in a known group with at least one anchor and no more clicks than anchors', () => {
    expect(TOOL_IDS).toHaveLength(50);
    const groups = GROUPS.map((g) => g.id);
    for (const id of TOOL_IDS) {
      expect(groups).toContain(TOOLS[id].group);
      expect(TOOLS[id].anchors).toBeGreaterThanOrEqual(1);
      expect(TOOLS[id].clicks).toBeLessThanOrEqual(TOOLS[id].anchors);
    }
  });
  it('a position tool takes two clicks and holds three anchors', () => {
    expect(TOOLS.long).toMatchObject({ anchors: 3, clicks: 2 });
    expect(TOOLS.short).toMatchObject({ anchors: 3, clicks: 2 });
  });
  it('newDrawing refuses the wrong number of points and gives each drawing its own id', () => {
    expect(() => newDrawing('trend', [a(1, 1)])).toThrow(DrawingError);
    const ids = new Set(Array.from({ length: 50 }, () => trend().id));
    expect(ids.size).toBe(50);
  });
});

describe('validateDoc', () => {
  it('reads back what serializeDoc wrote', () => {
    const d = newDrawing('text', [a(5, 6)], { color: '#ff9800', width: 3, dash: 'dashed', extendRight: true }, 'hello');
    const back = validateDoc(doc([trend(), d]), 'sbin');
    expect(back.drawings).toHaveLength(2);
    expect(back.drawings[1]).toMatchObject({ tool: 'text', text: 'hello', style: { color: '#ff9800', width: 3, dash: 'dashed', extendRight: true } });
  });
  const bad = (mutate: (x: Record<string, any>) => void, message: RegExp) => {
    const x = doc([trend()]);
    mutate(x);
    expect(() => validateDoc(x)).toThrow(message);
  };
  it('refuses each kind of bad document with a reason', () => {
    expect(() => validateDoc(null)).toThrow(/not a drawings document/);
    expect(() => validateDoc({ version: 2 })).toThrow(/not a drawings document/);
    expect(() => validateDoc(doc([]), 'TCS')).toThrow(/another symbol/);
    bad((x) => (x.drawings[0].tool = 'laser'), /unknown tool/);
    bad((x) => x.drawings[0].points.pop(), /needs 2 points/);
    bad((x) => (x.drawings[0].points[0].p = 'ten'), /not a time and a price/);
    bad((x) => (x.drawings[0].points[0].t = null), /not a time and a price/);
    bad((x) => (x.drawings[0].style.color = 'red'), /#rrggbb/);
    bad((x) => (x.drawings[0].style.width = 9), /width/);
    bad((x) => (x.drawings[0].style.dash = 'wavy'), /line style/);
    bad((x) => (x.drawings[0].text = 'x'.repeat(MAX_TEXT + 1)), /at most 200/);
    bad((x) => x.drawings.push(structuredClone(x.drawings[0])), /share an id/);
    bad((x) => (x.drawings = 'no'), /must be a list/);
  });
  it('refuses more than 500 drawings', () => {
    const many = Array.from({ length: MAX_DRAWINGS + 1 }, () => trend());
    expect(() => validateDoc(doc(many))).toThrow(/at most 500/);
    expect(() => add(Array.from({ length: MAX_DRAWINGS }, () => trend()), trend())).toThrow(DrawingError);
  });
});

describe('list operations', () => {
  it('add, remove and removeAll return new lists and leave the old alone', () => {
    const one = trend();
    const base = [one];
    const more = add(base, trend());
    expect(base).toHaveLength(1);
    expect(more).toHaveLength(2);
    expect(remove(more, one.id)).toHaveLength(1);
    expect(more).toHaveLength(2);
    expect(removeAll()).toEqual([]);
  });

  it('update changes style, text and flags without touching the original', () => {
    const d = trend();
    const base = [d];
    const next = update(base, d.id, { style: { color: '#f23645', width: 4, extendRight: true }, locked: true, hidden: true, text: 'x' });
    expect(next[0].style).toMatchObject({ color: '#f23645', width: 4, extendRight: true, dash: 'solid' });
    expect(next[0]).toMatchObject({ locked: true, hidden: true, text: 'x' });
    expect(base[0].style.color).toBe('#2962ff');
    expect(update(next, d.id, { style: { extendRight: false } })[0].style.extendRight).toBeUndefined();
    expect(update(base, 'nope', { locked: true })).toBe(base);
  });

  it('moveBy shifts every anchor; moveHandle moves one; a locked drawing does neither', () => {
    const d = trend();
    const moved = moveBy([d], d.id, 50, -5)[0];
    expect(moved.points).toEqual([a(150, 5), a(250, 15)]);
    expect(d.points).toEqual([a(100, 10), a(200, 20)]);
    expect(moveHandle([d], d.id, 1, a(300, 30))[0].points).toEqual([a(100, 10), a(300, 30)]);
    expect(moveHandle([d], d.id, 5, a(0, 0))[0]).toEqual(d);
    const locked = update([d], d.id, { locked: true });
    expect(moveBy(locked, d.id, 50, 5)[0].points).toEqual(d.points);
    expect(moveHandle(locked, d.id, 0, a(0, 0))[0].points).toEqual(d.points);
  });

  it('duplicate puts a shifted, unlocked copy with a new id on top', () => {
    const d = update([trend()], 'x', {}).length ? trend() : trend();
    const locked = update([d], d.id, { locked: true });
    const r = duplicate(locked, d.id, 10, 1);
    expect(r.list).toHaveLength(2);
    expect(r.id).not.toBe(d.id);
    const copy = r.list[1];
    expect(copy.locked).toBe(false);
    expect(copy.points[0]).toEqual(a(110, 11));
    expect(copy.style).toEqual(d.style);
    expect(duplicate(locked, 'nope', 1, 1).id).toBeNull();
  });

  it('reorder moves a drawing to the front or the back of the draw order', () => {
    const [x, y, z] = [trend(), trend(), trend()];
    const list = [x, y, z];
    expect(reorder(list, x.id, 'front').map((d) => d.id)).toEqual([y.id, z.id, x.id]);
    expect(reorder(list, z.id, 'back').map((d) => d.id)).toEqual([z.id, x.id, y.id]);
    expect(list.map((d) => d.id)).toEqual([x.id, y.id, z.id]);
  });

  it('lock all and hide all change every drawing and return the same list when nothing changes', () => {
    const list = [trend(), trend()];
    const locked = setAllLocked(list, true);
    expect(locked.every((d) => d.locked)).toBe(true);
    expect(setAllLocked(locked, true)).toBe(locked);
    expect(setAllHidden(list, false)).toBe(list);
    expect(setAllHidden(list, true).every((d) => d.hidden)).toBe(true);
  });
});
