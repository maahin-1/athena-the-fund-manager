// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { GREY, SCALES, colourFor, scaleOf } from '../src/screens/colour';
import { HeatmapView } from '../src/screens/heatmap';
import type { Row } from '../src/screenlogic';

const rgb = (css: string): [number, number, number] => (css.match(/\d+/g) ?? []).slice(0, 3).map(Number) as [number, number, number];

describe('the heatmap colour scale', () => {
  const day = scaleOf('ret_1d');

  it('is grey for a figure that is missing, never the colour of zero', () => {
    expect(colourFor(null, day)).toBe(GREY);
    expect(colourFor(undefined as never, day)).toBe(GREY);
    expect(colourFor('text', day)).toBe(GREY);
    expect(colourFor(0, day)).not.toBe(GREY);
  });

  it('runs from red for a fall through the neutral for no change to green for a rise, and stops at the ends of the range', () => {
    const [r0, g0] = rgb(colourFor(-0.03, day));
    const [rz, gz] = rgb(colourFor(0, day));
    const [r1, g1] = rgb(colourFor(0.03, day));
    expect(r0).toBeGreaterThan(g0); // red
    expect(g1).toBeGreaterThan(r1); // green
    expect(Math.abs(rz - gz)).toBeLessThan(15); // grey-ish
    expect(colourFor(0.5, day)).toBe(colourFor(0.03, day)); // beyond the range is the end colour
    expect(colourFor(-0.5, day)).toBe(colourFor(-0.03, day));
  });

  it('gets steadily greener as the figure rises', () => {
    const greens = [-0.03, -0.01, 0, 0.01, 0.02, 0.03].map((v) => rgb(colourFor(v, day))[1] - rgb(colourFor(v, day))[0]);
    for (let i = 1; i < greens.length; i++) expect(greens[i]).toBeGreaterThan(greens[i - 1]);
  });

  it('has a wider range for a longer period, and a one-sided range for the distance from the 52-week high', () => {
    expect(scaleOf('ret_1y').hi).toBeGreaterThan(scaleOf('ret_1m').hi);
    const high = scaleOf('from_52w_high');
    expect(high.lo).toBeLessThan(0);
    expect(high.hi).toBe(0);
    expect(rgb(colourFor(0, high))[1]).toBeGreaterThan(rgb(colourFor(-0.4, high))[1]);
  });

  it('every scale names itself and its range for the legend', () => {
    for (const s of SCALES) {
      expect(s.label).not.toBe('');
      expect(s.legend).toMatch(/%/);
    }
    expect(scaleOf('nonsense').id).toBe('ret_1d'); // an unknown scale falls back to the first
  });
});

describe('the heatmap view', () => {
  const stock = (symbol: string, extra: Row = {}): Row => ({ symbol, name: `${symbol} Limited`, kind: 'stock', sector: 'Tech', industry: 'Software', market_cap_cr: 1000, traded_value_cr: 10, ret_1d: 0.01, ret_1m: -0.05, from_52w_high: -0.1, ...extra });
  const rows: Row[] = [
    stock('AAA', { market_cap_cr: 8000 }), stock('BBB', { market_cap_cr: 4000, sector: 'Banks', industry: 'Private banks' }), stock('CCC', { market_cap_cr: 2000, ret_1d: null }),
    stock('DDD', { market_cap_cr: 1000, sector: 'Banks', industry: 'PSU banks' }), stock('NOCAP', { market_cap_cr: null }), stock('EEE', { market_cap_cr: 500 }),
  ];
  const view = (onSelect = vi.fn()) => new HeatmapView(onSelect, () => ({ w: 800, h: 500 }));
  const tiles = (v: HeatmapView) => [...v.element.querySelectorAll<HTMLElement>('.hm-tile')];

  it('draws a tile for every stock with a market cap, the larger tiles for the larger companies', () => {
    const v = view();
    v.render(rows);
    expect(tiles(v).map((t) => t.dataset.symbol).sort()).toEqual(['AAA', 'BBB', 'CCC', 'DDD', 'EEE']);
    const w = (s: string) => parseFloat(tiles(v).find((t) => t.dataset.symbol === s)!.style.width) * parseFloat(tiles(v).find((t) => t.dataset.symbol === s)!.style.height);
    expect(w('AAA')).toBeGreaterThan(w('BBB'));
    expect(w('BBB')).toBeGreaterThan(w('EEE'));
  });

  it('lists the indices of the whole screen, biggest first, and limits the tiles to the one chosen', () => {
    const members = [
      stock('AAA', { indices: 'NIFTY 50|NIFTY 500|NIFTY BANK' }), stock('BBB', { indices: 'NIFTY 500|NIFTY BANK' }), stock('CCC', { indices: 'NIFTY 500' }),
      stock('DDD', { indices: 'NIFTY AUTO' }), stock('EEE', {}),
    ];
    const v = view();
    v.render(members.slice(0, 2), {}, members);
    const picker = v.element.querySelector<HTMLSelectElement>('[aria-label="heatmap index"]')!;
    expect([...picker.options].map((o) => o.value)).toEqual(['', 'NIFTY 500', 'NIFTY BANK', 'NIFTY 50', 'NIFTY AUTO']);
    expect(picker.options[1].textContent).toBe('NIFTY 500 (3)');
    picker.value = 'NIFTY AUTO';
    picker.dispatchEvent(new Event('change'));
    expect(tiles(v).length).toBe(0); // AAA and BBB are not in it
    expect(v.element.querySelector('.hm-note')!.textContent).toContain('No stock in this result is in NIFTY AUTO');
    picker.value = 'NIFTY BANK';
    picker.dispatchEvent(new Event('change'));
    expect(tiles(v).map((t) => t.dataset.symbol).sort()).toEqual(['AAA', 'BBB']);
    expect(v.element.querySelector('.hm-note')!.textContent).toContain('in NIFTY BANK');
    v.render(members.slice(0, 2), {}, members); // the choice is kept while it is in the screen
    expect(picker.value).toBe('NIFTY BANK');
    v.render(members.slice(2), {}, members.slice(2)); // the screen no longer has the index: back to every stock in the result
    expect(picker.value).toBe('');
    expect(tiles(v).map((t) => t.dataset.symbol).sort()).toEqual(['CCC', 'DDD', 'EEE']);
  });

  it('groups by sector or industry, or not at all', () => {
    const v = view();
    v.render(rows, { group: 'sector' });
    expect([...v.element.querySelectorAll('.hm-glabel')].map((g) => g.textContent).sort()).toEqual(['Banks', 'Tech']);
    v.render(rows, { group: 'industry' });
    expect(v.element.querySelectorAll('.hm-group').length).toBe(3); // Software, Private banks, PSU banks
    v.render(rows, { group: 'none' });
    expect(v.element.querySelectorAll('.hm-group').length).toBe(0);
  });

  it('colours by the chosen figure, grey where a stock has none, and says what the colours mean', () => {
    const v = view();
    v.render(rows, { colour: 'ret_1d' });
    const day = Object.fromEntries(tiles(v).map((t) => [t.dataset.symbol, t.style.background]));
    expect(day.CCC).toBe(GREY); // no 1-day figure
    v.render(rows, { colour: 'ret_1m' });
    const month = Object.fromEntries(tiles(v).map((t) => [t.dataset.symbol, t.style.background]));
    expect(month.AAA).not.toBe(day.AAA); // +1% on the day, -5% on the month
    expect(v.element.querySelector('.hm-legend')?.textContent).toContain('1 month');
  });

  it('sizes by traded value when asked', () => {
    const v = view();
    v.render([stock('X', { market_cap_cr: 100, traded_value_cr: 900 }), stock('Y', { market_cap_cr: 900, traded_value_cr: 100 })], { size: 'traded_value_cr', group: 'none' });
    const area = (s: string) => parseFloat(tiles(v).find((t) => t.dataset.symbol === s)!.style.width) * parseFloat(tiles(v).find((t) => t.dataset.symbol === s)!.style.height);
    expect(area('X')).toBeGreaterThan(area('Y'));
  });

  it('keeps only the largest when there are more stocks than it will draw', () => {
    const v = view();
    const many = Array.from({ length: 40 }, (_, i) => stock(`S${i}`, { market_cap_cr: 100 + i }));
    v.render(many, { limit: 10 });
    expect(tiles(v)).toHaveLength(10);
    expect(tiles(v).some((t) => t.dataset.symbol === 'S39')).toBe(true);
    expect(tiles(v).some((t) => t.dataset.symbol === 'S0')).toBe(false);
    expect(v.element.querySelector('.hm-note')?.textContent).toContain('largest 10 of 40');
  });

  it('gives a tooltip with the name and figures, and tells the page which stock was clicked', () => {
    const pick = vi.fn();
    const v = view(pick);
    v.render(rows);
    const aaa = tiles(v).find((t) => t.dataset.symbol === 'AAA')!;
    expect(aaa.title).toContain('AAA Limited');
    expect(aaa.title).toContain('Tech');
    aaa.click();
    expect(pick).toHaveBeenCalledWith('AAA', rows[0]);
  });

  it('writes a symbol on a tile only where the whole symbol fits', () => {
    const v = view();
    const many = [stock('BIGONE', { market_cap_cr: 100000 }), ...Array.from({ length: 60 }, (_, i) => stock(`T${i}`, { market_cap_cr: 10 }))];
    v.render(many, { group: 'none' });
    expect(tiles(v).find((t) => t.dataset.symbol === 'BIGONE')!.querySelector('.hm-sym')?.textContent).toBe('BIGONE');
    expect(tiles(v).find((t) => t.dataset.symbol === 'T5')!.querySelector('.hm-sym')).toBeNull();
  });

  it('says so when there is nothing to draw', () => {
    const v = view();
    v.render([stock('NOCAP', { market_cap_cr: null })]);
    expect(tiles(v)).toHaveLength(0);
    expect(v.element.textContent).toContain('No stock in this result has a market cap to size a tile by');
  });
});
