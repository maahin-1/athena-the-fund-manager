import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { Projection, snap, type Axes } from '../src/chartlab/drawings/projection';

/** Bars at index i sit at x = 10 i + 5; price p is at y = 400 - 2 p. */
const linear: Axes = { x: (i) => i * 10 + 5, index: (x) => (x - 5) / 10, y: (p) => 400 - 2 * p, price: (y) => (400 - y) / 2 };
const times = [1000, 2000, 3000, 4000];
const proj = () => new Projection(times, 1000, linear);

describe('Projection', () => {
  it('puts a bar time on its bar and a time between two bars between them', () => {
    expect(proj().x(1000)).toBe(5);
    expect(proj().x(3000)).toBe(25);
    expect(proj().x(2500)).toBe(20); // half way between bars 1 and 2
    expect(proj().indexOf(2500)).toBe(1.5);
  });

  it('extrapolates by whole steps beyond the last and before the first bar', () => {
    expect(proj().indexOf(6000)).toBe(5);
    expect(proj().x(6000)).toBe(55);
    expect(proj().indexOf(4500)).toBe(3.5);
    expect(proj().indexOf(0)).toBe(-1);
    expect(proj().x(0)).toBe(-5);
  });

  it('goes from a pixel back to the nearest bar time and a whole step beyond the ends', () => {
    expect(proj().t(5)).toBe(1000);
    expect(proj().t(24)).toBe(3000);
    expect(proj().t(19)).toBe(2000); // 1.4 rounds to bar 1
    expect(proj().t(21)).toBe(3000);
    expect(proj().t(55)).toBe(6000);
    expect(proj().t(-15)).toBe(-1000);
  });

  it('a time taken to a pixel and back is the same time for any bar and any step beyond', () => {
    for (const t of [1000, 2000, 3000, 4000, 5000, 9000, -2000]) expect(proj().t(proj().x(t) as number)).toBe(t);
  });

  it('works for a different interval: the same time lands between different bars', () => {
    const weekly = new Projection([0, 604_800, 1_209_600], 604_800, linear);
    expect(weekly.indexOf(302_400)).toBe(0.5);
    const hourly = new Projection([0, 3600, 7200, 10_800, 14_400], 3600, linear);
    expect(hourly.indexOf(302_400)).toBeGreaterThan(4); // far past the last hour
  });

  it('converts prices both ways and gives null when the chart has nothing to say', () => {
    expect(proj().y(100)).toBe(200);
    expect(proj().price(200)).toBe(100);
    const none = new Projection(times, 1000, { x: () => null, index: () => null, y: () => null, price: () => null });
    expect(none.x(1000)).toBeNull();
    expect(none.t(5)).toBeNull();
    expect(none.y(1)).toBeNull();
    const empty = new Projection([], 1000, linear);
    expect(empty.ready).toBe(false);
    expect(empty.x(1)).toBeNull();
    expect(empty.t(1)).toBeNull();
  });
});

describe('magnet', () => {
  const bars: Bar[] = times.map((t, i) => ({ t, o: 100 + i, h: 110 + i, l: 90 + i, c: 105 + i, v: 1 }));
  // bar 1: o 101 -> y 198, h 111 -> y 178, l 91 -> y 218, c 106 -> y 188
  it('off keeps the pointer price and snaps only the time', () => {
    const a = snap(proj(), bars, 17, 150, 'off')!;
    expect(a.t).toBe(2000);
    expect(a.p).toBe(125);
  });

  it('weak snaps to the nearest of open, high, low and close within 12 pixels, and not beyond', () => {
    const near = snap(proj(), bars, 15, 181, 'weak')!; // 3 px from the high
    expect(near).toEqual({ t: 2000, p: 111 });
    const far = snap(proj(), bars, 15, 100, 'weak')!; // 78 px from the high
    expect(far.p).toBe(150);
    expect(snap(proj(), bars, 15, 178 - 12, 'weak')!.p).toBe(111); // exactly 12 px: snaps
    expect(snap(proj(), bars, 15, 178 - 13, 'weak')!.p).toBe((400 - 165) / 2);
  });

  it('strong always snaps to the nearest of the four prices', () => {
    expect(snap(proj(), bars, 15, 100, 'strong')).toEqual({ t: 2000, p: 111 });
    expect(snap(proj(), bars, 15, 330, 'strong')).toEqual({ t: 2000, p: 91 });
  });

  it('beyond the last bar there is nothing to snap to, so the pointer price stays', () => {
    const a = snap(proj(), bars, 75, 150, 'strong')!;
    expect(a.t).toBe(8000);
    expect(a.p).toBe(125);
  });

  it('returns null when the chart cannot say', () => {
    const none = new Projection(times, 1000, { x: () => null, index: () => null, y: () => null, price: () => null });
    expect(snap(none, bars, 1, 1, 'weak')).toBeNull();
  });
});
