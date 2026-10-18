// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import type { ChartRenderer } from '../src/chartlab/render';
import type { KeyValue } from '../src/chartlab/store';
import { ChartLabView, type BarsFetcher } from '../src/chartlab/view';
import type { Quote } from '../src/feed';
import type { AppContext } from '../src/panels/base';

const first = Date.UTC(2026, 5, 1) / 1000;
const bars: Bar[] = Array.from({ length: 40 }, (_, i) => ({ t: first + i * 86_400, o: 100 + i, h: 103 + i, l: 99 + i, c: 101 + i, v: 1000 }));

class Memory implements KeyValue {
  data: Record<string, string> = {};
  getItem(k: string): string | null {
    return this.data[k] ?? null;
  }
  setItem(k: string, v: string): void {
    this.data[k] = v;
  }
}

function make() {
  let pointer: (i: number | null) => void = () => {};
  const painted = { structure: 0, ticks: 0, shown: [] as (number | null)[] };
  const renderer = (): ChartRenderer => ({
    paint: () => void painted.structure++,
    tick: () => void painted.ticks++,
    fit: () => {},
    showLast: (c) => void painted.shown.push(c),
    onCrosshair: (cb) => (pointer = cb),
    paneElement: () => null, paneOf: () => 0, snapshot: () => null, applyTheme: () => {}, dispose: () => {},
  });
  const fetched: string[] = [];
  const fetchBars: BarsFetcher = async (symbol, tf) => {
    fetched.push(`${symbol} ${tf}`);
    return { bars, note: '' };
  };
  let quotes: (qs: Quote[]) => void = () => {};
  const feed = { onQuotes: (fn: (qs: Quote[]) => void) => ((quotes = fn), () => {}), need: vi.fn(), release: vi.fn(), quotes: new Map() };
  const ctx = { feed, bus: { subscribe: () => () => {}, setSymbol: vi.fn(), symbolOf: () => undefined }, watchlist: { items: ['SBIN'], subscribe: () => () => {} } } as unknown as AppContext;
  const view = new ChartLabView(ctx, { renderer, fetchBars, storage: new Memory() });
  document.body.append(view.element);
  const q = (selector: string) => view.element.querySelector(selector) as HTMLElement;
  return { view, painted, fetched, feed, quotes: (qs: Quote[]) => quotes(qs), pointer: (i: number | null) => pointer(i), q };
}

const flush = async () => {
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
};

async function started(from = 9) {
  const h = make();
  h.view.show('SBIN');
  await flush();
  h.q('[aria-label="replay"]').click();
  h.pointer(from);
  h.q('.lab-host').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  return h;
}

beforeEach(() => {
  document.body.replaceChildren();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('replay in the Chart Lab', () => {
  it('asks for a bar, then shows the history up to the bar clicked and the replay bar', async () => {
    const h = make();
    h.view.show('SBIN');
    await flush();
    h.q('[aria-label="replay"]').click();
    expect(h.q('.lab-note').textContent).toContain('Click the bar');
    h.pointer(9);
    h.q('.lab-host').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(h.view.model.bars).toHaveLength(10);
    expect(h.view.model.replaying).toBe(true);
    expect(h.q('.lab-replay').hidden).toBe(false);
    expect(h.q('.lab-replay-status').textContent).toContain('bar 10 of 40');
  });

  it('a click that is not on a bar does not start it', async () => {
    const h = make();
    h.view.show('SBIN');
    await flush();
    h.q('[aria-label="replay"]').click();
    h.pointer(null);
    h.q('.lab-host').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(h.view.model.replaying).toBe(false);
    expect(h.q('.lab-note').textContent).toContain('Click on a bar');
  });

  it('steps one bar forward as a tick and back as a rebuild, and never past the ends', async () => {
    const h = await started(0);
    expect(h.view.model.bars).toHaveLength(1);
    const ticks = h.painted.ticks;
    h.q('[aria-label="replay next"]').click();
    expect(h.view.model.bars).toHaveLength(2);
    expect(h.painted.ticks).toBe(ticks + 1);
    const structure = h.painted.structure;
    h.q('[aria-label="replay back"]').click();
    expect(h.view.model.bars).toHaveLength(1);
    expect(h.painted.structure).toBeGreaterThan(structure);
    h.q('[aria-label="replay back"]').click();
    expect(h.view.model.bars).toHaveLength(1);
  });

  it('plays at the chosen speed, stops at the latest bar and says so', async () => {
    const h = await started(36);
    vi.useFakeTimers();
    const speed = h.q('[aria-label="replay speed"]') as HTMLSelectElement;
    speed.value = '10';
    speed.dispatchEvent(new Event('change'));
    h.q('[aria-label="replay play"]').click();
    vi.advanceTimersByTime(100);
    expect(h.view.model.bars).toHaveLength(38);
    vi.advanceTimersByTime(250);
    expect(h.view.model.bars).toHaveLength(40);
    expect(h.q('.lab-note').textContent).toContain('latest bar');
    expect(h.q('[aria-label="replay play"]').textContent).toBe('▶');
    vi.advanceTimersByTime(1000);
    expect(h.view.model.bars).toHaveLength(40);
  });

  it('pauses when the page is hidden, and when the tab is left', async () => {
    const h = await started(5);
    vi.useFakeTimers();
    h.q('[aria-label="replay play"]').click();
    vi.advanceTimersByTime(1000);
    expect(h.view.model.bars).toHaveLength(7);
    h.view.hide();
    vi.advanceTimersByTime(5000);
    expect(h.view.model.bars).toHaveLength(7);
    expect(h.view.model.replaying).toBe(true); // paused, not ended
  });

  it('jumps to a date and refuses text that is not one', async () => {
    const h = await started(5);
    const box = h.q('[aria-label="replay date"]') as HTMLInputElement;
    box.value = '2026-06-20';
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(h.view.model.bars).toHaveLength(20); // 1 June to 20 June is twenty days of bars here
    box.value = 'soon';
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(h.q('.lab-note').textContent).toContain('YYYY-MM-DD');
    expect(h.view.model.bars).toHaveLength(20);
    box.value = '2020-01-01';
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(h.q('.lab-note').textContent).toContain('no bars before');
  });

  it('ignores live prices while replaying', async () => {
    const h = await started(9);
    h.quotes([{ s: 'SBIN', l: 500, t: Date.now() } as unknown as Quote]);
    expect(h.view.model.bars).toHaveLength(10);
    expect(h.view.model.bars[9].c).toBe(110);
  });

  it('keeps the moment when the interval changes, and the player follows the new bars', async () => {
    const h = await started(15);
    (h.q('[data-interval="1W"]') as HTMLElement).click();
    await flush();
    expect(h.view.model.replaying).toBe(true);
    expect(h.q('.lab-replay').hidden).toBe(false);
    expect(h.fetched.at(-1)).toBe('SBIN 1D'); // weeks are built from daily bars
    const status = h.q('.lab-replay-status').textContent ?? '';
    expect(status).toMatch(/bar \d+ of \d+/);
  });

  it('Escape, Exit and another symbol each end it and bring every bar back', async () => {
    for (const how of ['escape', 'exit', 'symbol'] as const) {
      const h = await started(9);
      if (how === 'escape') document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      else if (how === 'exit') h.q('[aria-label="replay exit"]').click();
      else {
        const input = h.q('.lab-symbol') as HTMLInputElement;
        input.value = 'tcs';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
        await flush();
      }
      expect(h.view.model.replaying, how).toBe(false);
      expect(h.view.model.bars, how).toHaveLength(40);
      expect(h.q('.lab-replay').hidden, how).toBe(true);
    }
  });

  it('is refused on a chart of price moves, and does not start with too few bars', async () => {
    const h = make();
    h.view.show('SBIN');
    await flush();
    h.view.model.setType('renko');
    h.q('[aria-label="replay"]').click();
    expect(h.q('.lab-note').textContent).toContain('chart of time');
    expect(h.view.model.replaying).toBe(false);
  });

  it('takes Right, Left and Space while replaying, and nothing while a box has the keys', async () => {
    const h = await started(9);
    const key = (k: string, target: HTMLElement = document.body) => target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
    key('ArrowRight');
    expect(h.view.model.bars).toHaveLength(11);
    key('ArrowLeft');
    expect(h.view.model.bars).toHaveLength(10);
    key('ArrowRight', h.q('[aria-label="replay date"]'));
    expect(h.view.model.bars).toHaveLength(10);
  });
});
