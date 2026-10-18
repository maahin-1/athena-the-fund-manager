import { describe, expect, it } from 'vitest';
import { Creator } from '../src/chartlab/drawings/creator';
import { TOOL_IDS, TOOLS } from '../src/chartlab/drawings/model';

const a = (t: number, p: number) => ({ t, p });

describe('Creator', () => {
  it('every tool is finished by exactly its number of clicks and holds its number of anchors', () => {
    for (const id of TOOL_IDS) {
      const c = new Creator();
      c.begin(id);
      let done = null;
      for (let i = 0; i < TOOLS[id].clicks; i++) {
        expect(done, `${id} finished early`).toBeNull();
        done = c.click(a(100 + i * 10, 50 + i));
      }
      expect(done, id).not.toBeNull();
      expect(done!.points, id).toHaveLength(TOOLS[id].anchors);
      expect(c.active, `${id} stays chosen after finishing`).toBeNull();
    }
  });

  it('keeps the anchors in the order they were clicked', () => {
    const c = new Creator();
    c.begin('channel');
    c.click(a(1, 10));
    c.click(a(2, 20));
    const d = c.click(a(3, 30))!;
    expect(d.points).toEqual([a(1, 10), a(2, 20), a(3, 30)]);
  });

  it('a click with no tool does nothing', () => {
    expect(new Creator().click(a(1, 1))).toBeNull();
  });

  it('the preview follows the pointer and needs a tool and a pointer', () => {
    const c = new Creator();
    expect(c.preview()).toBeNull();
    c.begin('trend');
    expect(c.preview()).toBeNull();
    c.move(a(5, 50));
    expect(c.preview()!.points).toEqual([a(5, 50), a(5, 50)]); // nothing placed yet: both ends at the pointer
    c.click(a(1, 10));
    c.move(a(9, 90));
    expect(c.preview()!.points).toEqual([a(1, 10), a(9, 90)]);
    expect(c.count).toBe(1);
  });

  it('a three-anchor tool previews with the pointer as the anchors still to come', () => {
    const c = new Creator();
    c.begin('triangle');
    c.click(a(1, 10));
    c.move(a(2, 20));
    expect(c.preview()!.points).toEqual([a(1, 10), a(2, 20), a(2, 20)]);
    c.click(a(2, 20));
    c.move(a(3, 5));
    expect(c.preview()!.points).toEqual([a(1, 10), a(2, 20), a(3, 5)]);
  });

  it('a one-click tool previews at the pointer before the click', () => {
    const c = new Creator();
    c.begin('hline');
    c.move(a(7, 70));
    expect(c.preview()!.points).toEqual([a(7, 70)]);
  });

  it('a position tool ends in entry, mirrored stop and target', () => {
    const c = new Creator();
    c.begin('long');
    c.click(a(10, 100));
    c.move(a(30, 115));
    const p = c.preview()!;
    expect(p.points).toEqual([a(10, 100), a(30, 85), a(30, 115)]);
    const d = c.click(a(30, 115))!;
    expect(d.points).toEqual([a(10, 100), a(30, 85), a(30, 115)]);
    c.begin('short');
    c.click(a(10, 100));
    expect(c.click(a(30, 90))!.points).toEqual([a(10, 100), a(30, 110), a(30, 90)]);
  });

  it('Escape (cancel) drops a half-drawn tool, and choosing another tool drops the old one', () => {
    const c = new Creator();
    c.begin('trend');
    c.click(a(1, 1));
    c.cancel();
    expect(c.active).toBeNull();
    expect(c.count).toBe(0);
    c.begin('trend');
    c.click(a(1, 1));
    c.begin('rect');
    expect(c.count).toBe(0);
    expect(c.active).toBe('rect');
    expect(c.click(a(5, 5))).toBeNull();
    expect(c.click(a(6, 6))!.tool).toBe('rect');
  });

  it('the drawing takes the style it is given', () => {
    const c = new Creator();
    c.begin('hline');
    const d = c.click(a(1, 1), { color: '#f23645', width: 3 })!;
    expect(d.style).toMatchObject({ color: '#f23645', width: 3, dash: 'solid' });
  });
});
