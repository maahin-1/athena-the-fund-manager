// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import type { AppContext } from '../src/panels/base';
import { ApiError, type ChainStructure, type OptionsApi } from '../src/options/api';
import { OptionsView } from '../src/options/view';
import { lineChart, extent, ticks } from '../src/options/svgchart';

const IST = 19_800;
const now = Date.UTC(2026, 9, 27, 11, 0) / 1000 - IST;
const flush = async () => {
  for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0));
};

const ref = (token: string, lot = 65) => ({ token, symbol: `S${token}`, lot });
const structure = (underlying: string, expiry: string): ChainStructure => ({
  underlying, expiry, segment: 'NFO', class: 'index', lot: 65, tick: 0.05, future: ref(`F${expiry}`),
  strikes: Array.from({ length: 41 }, (_, i) => 23_000 + i * 100).map((strike) => ({ strike, call: ref(`C${strike}`), put: ref(`P${strike}`) })),
});

function stubApi(overrides: Partial<OptionsApi> = {}): OptionsApi & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    underlyings: async () => [
      { underlying: 'NIFTY', segment: 'NFO', class: 'index', odd: false, expiries: 3, lot: 65, futures: true, options: true },
      { underlying: 'GOLD', segment: 'MCX', class: 'commodity', odd: false, expiries: 2, lot: 100, futures: true, options: true },
      { underlying: 'NOOPT', segment: 'NFO', class: 'stock', odd: false, expiries: 1, lot: 10, futures: true, options: false },
    ],
    expiries: async (u) => (calls.push(`expiries ${u}`), [
      { date: '2026-11-03', futures: true, options: true, series: 'weekly' as const },
      { date: '2026-11-26', futures: true, options: true, series: 'monthly' as const },
    ]),
    chain: async (u, e) => (calls.push(`chain ${u} ${e}`), structure(u, e)),
    futures: async () => [{ expiry: '2026-11-26', token: 'F1', symbol: 'NIFTY26NOVFUT', lot: 65, tick: 0.05, segment: 'NFO' }],
    ...overrides,
  };
}

class Memory {
  data: Record<string, string> = {};
  getItem(k: string) { return this.data[k] ?? null; }
  setItem(k: string, v: string) { this.data[k] = v; }
}

function make(api = stubApi(), storage = new Memory()) {
  const view = new OptionsView({} as AppContext, { api, storage, now: () => now });
  document.body.replaceChildren(view.element);
  return { view, api, storage };
}

beforeEach(() => document.body.replaceChildren());

describe('the line chart', () => {
  it('draws lines, tick labels, marks and a zero line, and says when there is nothing to draw', () => {
    const svg = lineChart({
      label: 'test', lines: [{ cls: 'a', points: [{ x: 0, y: -5 }, { x: 10, y: 5 }] }], marks: [{ x: 5, label: 'F', cls: 'forward' }], zero: true,
      x: (v) => String(v), y: (v) => String(v),
    });
    expect(svg.getAttribute('aria-label')).toBe('test');
    expect(svg.querySelectorAll('polyline.opt-line,circle.opt-line').length).toBe(1);
    expect(svg.querySelectorAll('.opt-mark').length).toBe(1);
    expect(svg.querySelector('.opt-zero')).not.toBeNull();
    const empty = lineChart({ label: 'none', lines: [{ cls: 'a', points: [] }], x: String, y: String });
    expect(empty.querySelectorAll('polyline.opt-line,circle.opt-line').length).toBe(0);
    expect(empty.querySelector('.opt-empty')).not.toBeNull();
  });

  it('picks round tick values and pads a flat extent', () => {
    const t = ticks(0, 100, 4);
    expect(t[0]).toBeGreaterThanOrEqual(0);
    expect(t[t.length - 1]).toBeLessThanOrEqual(100);
    expect(t.length).toBeGreaterThanOrEqual(3);
    const [lo, hi] = extent([7, 7, 7]);
    expect(lo).toBeLessThan(7);
    expect(hi).toBeGreaterThan(7);
  });
});

describe('the Options page', () => {
  it('shows nothing until asked, then loads the underlying, its expiries, the chain and the futures', async () => {
    const { view, api } = make();
    expect(view.element.hidden).toBe(true);
    expect(api.calls).toEqual([]);
    view.show();
    await flush();
    expect(view.element.hidden).toBe(false);
    expect(api.calls[0]).toBe('expiries NIFTY');
    expect(api.calls).toContain('chain NIFTY 2026-11-03');
    expect(view.element.querySelectorAll('tr.opt-row').length).toBeGreaterThan(10);
    expect(view.element.querySelectorAll('tr.atm').length).toBe(1);
    expect(view.element.querySelector('.opt-futtable tr[data-expiry="2026-11-26"]')).not.toBeNull();
    expect(view.element.querySelector('.opt-synth')!.textContent).toBe('synthetic prices');
  });

  it('shows the statistics: spot, forward, ATM, ATM IV, both put-call ratios, max pain, days and lot', async () => {
    const { view } = make();
    view.show();
    await flush();
    const stat = (k: string) => view.element.querySelector(`[data-stat="${k}"] .v`)!.textContent;
    for (const k of ['Spot', 'Forward', 'ATM', 'ATM IV', 'PCR (OI)', 'PCR (vol)', 'Max pain', 'Days', 'Lot']) expect(stat(k), k).not.toBe('–');
    expect(stat('Lot')).toBe('65');
    expect(Number(stat('Days'))).toBeCloseTo(7.2, 0);
    expect(stat('ATM IV')).toMatch(/^\d+\.\d%$/);
  });

  it('draws the volatility smile and, after the chains of the next expiries are read, the term curve', async () => {
    const { view, api } = make();
    view.show();
    await flush();
    expect(view.element.querySelector('svg[aria-label="Implied volatility by strike"] polyline.opt-line,circle.opt-line')).not.toBeNull();
    expect(view.element.querySelector('svg[aria-label="At-the-money implied volatility by expiry"] polyline.opt-line,circle.opt-line')).not.toBeNull();
    expect(api.calls).toContain('chain NIFTY 2026-11-26');
  });

  it('limits the strikes shown to the count chosen, and shows Greeks only when asked', async () => {
    const { view } = make();
    view.show();
    await flush();
    const all = () => view.element.querySelectorAll('tr.opt-row').length;
    const sel = view.element.querySelector<HTMLSelectElement>('[aria-label="strikes shown"]')!;
    sel.value = 'all';
    sel.dispatchEvent(new Event('change'));
    expect(all()).toBe(41);
    sel.value = '10';
    sel.dispatchEvent(new Event('change'));
    expect(all()).toBe(21);
    expect(view.element.querySelector('td[data-col="delta"]')).toBeNull();
    const box = view.element.querySelector<HTMLInputElement>('[aria-label="show Greeks"]')!;
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    expect(view.element.querySelectorAll('td[data-col="delta"]').length).toBeGreaterThan(20);
    expect(view.element.querySelectorAll('td[data-col="theta"]').length).toBeGreaterThan(20);
  });

  it('keeps its choices between visits', async () => {
    const storage = new Memory();
    const first = make(stubApi(), storage);
    first.view.show();
    await flush();
    const sel = first.view.element.querySelector<HTMLSelectElement>('[aria-label="strikes shown"]')!;
    sel.value = '40';
    sel.dispatchEvent(new Event('change'));
    const box = first.view.element.querySelector<HTMLInputElement>('[aria-label="show Greeks"]')!;
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    const second = make(stubApi(), storage);
    expect(second.view.element.querySelector<HTMLSelectElement>('[aria-label="strikes shown"]')!.value).toBe('40');
    expect(second.view.element.querySelector<HTMLInputElement>('[aria-label="show Greeks"]')!.checked).toBe(true);
  });

  it('opens on the underlying asked for, ignores one with no options and says so for one not listed', async () => {
    const { view, api } = make();
    view.show('gold');
    await flush();
    expect(api.calls[0]).toBe('expiries GOLD');
    const input = view.element.querySelector<HTMLInputElement>('[aria-label="underlying"]')!;
    input.value = 'noopt';
    input.dispatchEvent(new Event('change'));
    await flush();
    expect(view.element.querySelector('.opt-note')!.textContent).toMatch(/NOOPT has no option contracts/);
    expect(input.value).toBe('noopt');
    input.value = 'nope';
    input.dispatchEvent(new Event('change'));
    await flush();
    expect(view.element.querySelector('.opt-note')!.textContent).toMatch(/NOPE has no option contracts/);
    expect(view.element.querySelectorAll('tr.opt-row').length).toBeGreaterThan(0);
  });

  it('changes the chain when another expiry is chosen', async () => {
    const { view, api } = make();
    view.show();
    await flush();
    api.calls.length = 0;
    const sel = view.element.querySelector<HTMLSelectElement>('[aria-label="expiry"]')!;
    sel.value = '2026-11-26';
    sel.dispatchEvent(new Event('change'));
    await flush();
    expect(api.calls[0]).toBe('chain NIFTY 2026-11-26');
    expect(Number(view.element.querySelector('[data-stat="Days"] .v')!.textContent)).toBeGreaterThan(25);
  });

  it('says what the server said when the contract list is not ready', async () => {
    const api = stubApi({ underlyings: async () => { throw new ApiError('the contract list is still loading', 503); } });
    const { view } = make(api);
    view.show();
    await flush();
    expect(view.element.querySelector('.opt-note')!.textContent).toMatch(/still loading.*Trying again/);
    view.hide();
  });

  it('shows the error of a chain that could not be read and leaves the page standing', async () => {
    const api = stubApi({ chain: async () => { throw new ApiError('unknown expiry', 404); } });
    const { view } = make(api);
    view.show();
    await flush();
    expect(view.element.querySelector('.opt-note')!.textContent).toMatch(/unknown expiry/);
  });
});

describe('the payoff builder on the page', () => {
  async function ready() {
    const m = make();
    m.view.show();
    await flush();
    return m;
  }
  const atmRow = (v: OptionsView) => v.element.querySelector('tr.atm')!;
  const cell = (row: Element, side: string, col: string) => row.querySelector<HTMLElement>(`td[data-side="${side}"][data-col="${col}"]`)!;

  it('starts empty, and a click on a price adds that contract as a buy at the ask, a shift-click as a sale at the bid', async () => {
    const { view } = await ready();
    expect(view.element.querySelector('.opt-legs')).toBeNull();
    const row = atmRow(view);
    cell(row, 'call', 'ltp').click();
    cell(row, 'put', 'ltp').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
    const legs = view.element.querySelectorAll('.opt-legs tr');
    expect(legs.length).toBe(2);
    expect(legs[0].textContent).toContain('Buy');
    expect(legs[1].textContent).toContain('Sell');
  });

  it('a short straddle has unlimited loss and two break-evens around the strike; flipping a leg changes the position', async () => {
    const { view } = await ready();
    const row = atmRow(view);
    cell(row, 'call', 'ltp').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
    cell(row, 'put', 'ltp').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
    const line = (k: string) => view.element.querySelector(`[data-key="${k}"] .v`)!.textContent!;
    expect(line('Max loss')).toBe('unlimited');
    expect(line('Net premium')).toMatch(/received/);
    expect(line('Break-even').split(' · ').length).toBe(2);
    expect(view.element.querySelector('svg[aria-label="Profit and loss of the position at expiry and now"]')).not.toBeNull();
    const strike = Number(row.getAttribute('data-strike'));
    const [lo, hi] = line('Break-even').split(' · ').map((s) => Number(s.replace(/[^0-9.]/g, '')));
    expect(lo).toBeLessThan(strike);
    expect(hi).toBeGreaterThan(strike);
    const flip = view.element.querySelector<HTMLButtonElement>(`[aria-label="flip call ${strike}"]`)!;
    flip.click();
    expect(view.element.querySelector(`[data-key="Max loss"] .v`)!.textContent).not.toBe('unlimited');
  });

  it('refuses a leg of another expiry with the reason, and a clear empties the position', async () => {
    const { view } = await ready();
    cell(atmRow(view), 'call', 'ltp').click();
    const sel = view.element.querySelector<HTMLSelectElement>('[aria-label="expiry"]')!;
    sel.value = '2026-11-26';
    sel.dispatchEvent(new Event('change'));
    await flush();
    cell(atmRow(view), 'call', 'ltp').click();
    expect(view.element.querySelectorAll('.opt-legs tr').length).toBe(1);
    expect(view.element.querySelector('.opt-message')!.textContent).toMatch(/share one expiry/);
    view.element.querySelector<HTMLButtonElement>('[aria-label="clear legs"]')!.click();
    expect(view.element.querySelector('.opt-legs')).toBeNull();
  });

  it('changing lots changes the units and the premium; a typed price replaces the entry price', async () => {
    const { view } = await ready();
    const row = atmRow(view);
    cell(row, 'call', 'ltp').click();
    const strike = row.getAttribute('data-strike');
    const premium = () => view.element.querySelector('[data-key="Net premium"] .v')!.textContent!;
    const one = premium();
    const lots = view.element.querySelector<HTMLInputElement>(`[aria-label="lots call ${strike}"]`)!;
    lots.value = '2';
    lots.dispatchEvent(new Event('change'));
    const two = premium();
    const num = (s: string) => Number(s.replace(/[^0-9.]/g, ''));
    expect(num(two)).toBeCloseTo(num(one) * 2, 0);
    const px = view.element.querySelector<HTMLInputElement>(`[aria-label="price call ${strike}"]`)!;
    px.value = '100';
    px.dispatchEvent(new Event('change'));
    expect(num(premium())).toBeCloseTo(100 * 2 * 65, 0);
  });

  it('adds a future at the forward and a long future has unlimited profit and a finite loss', async () => {
    const { view } = await ready();
    view.element.querySelector<HTMLButtonElement>('[aria-label="add future buy"]')!.click();
    expect(view.element.querySelector('[data-key="Max profit"] .v')!.textContent).toBe('unlimited');
    expect(view.element.querySelector('[data-key="Max loss"] .v')!.textContent).not.toBe('unlimited');
  });

  it('is cleared when another underlying is chosen, since the position belongs to the first', async () => {
    const { view } = await ready();
    cell(atmRow(view), 'call', 'ltp').click();
    expect(view.element.querySelectorAll('.opt-legs tr').length).toBe(1);
    const input = view.element.querySelector<HTMLInputElement>('[aria-label="underlying"]')!;
    input.value = 'GOLD';
    input.dispatchEvent(new Event('change'));
    await flush();
    expect(view.element.querySelector('.opt-legs')).toBeNull();
  });
});
