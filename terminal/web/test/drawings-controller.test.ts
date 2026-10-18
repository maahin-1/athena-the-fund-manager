// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Bar } from '../src/bars';
import { DrawingController } from '../src/chartlab/drawings/controller';
import type { Bounds } from '../src/chartlab/drawings/geometry';
import type { DrawingHost } from '../src/chartlab/drawings/host';
import { newDrawing } from '../src/chartlab/drawings/model';
import { Projection } from '../src/chartlab/drawings/projection';
import { ActionLog, DrawingStore, type DrawingIO } from '../src/chartlab/drawings/store';

/** Bar i at x = 10 i + 5 and time 1000 i; price p at y = 400 - 2 p; the pane is 600 x 400. */
const times = Array.from({ length: 60 }, (_, i) => i * 1000);
const bars: Bar[] = times.map((t, i) => ({ t, o: 100 + i, h: 110 + i, l: 90 + i, c: 105 + i, v: 1 }));
const size: Bounds = { w: 600, h: 400 };
const X = (i: number) => i * 10 + 5;
const Y = (p: number) => 400 - 2 * p;

function setup(opts: { enabled?: () => boolean; saved?: string | null } = {}) {
  const el = document.createElement('div');
  document.body.append(el);
  const proj = new Projection(times, 1000, { x: (i) => i * 10 + 5, index: (x) => (x - 5) / 10, y: (p) => 400 - 2 * p, price: (y) => (400 - y) / 2 });
  const state = { paint: null as null | ((c: CanvasRenderingContext2D, p: Projection, s: Bounds) => void), redraws: 0, frozen: [] as boolean[] };
  const host: DrawingHost = {
    projection: () => proj,
    size: () => size,
    onPaint: (fn) => (state.paint = fn),
    redraw: () => state.redraws++,
    freeze: (on) => state.frozen.push(on),
  };
  const saves: string[] = [];
  const io: DrawingIO = { get: async () => opts.saved ?? null, put: async (_s, text) => (saves.push(text), true) };
  const store = new DrawingStore(io, null);
  const log = new ActionLog();
  const c = new DrawingController({ element: el, host, store, log, bars: () => bars, enabled: opts.enabled ?? (() => true) });
  const fire = (type: string, x: number, y: number, extra: MouseEventInit = {}) => {
    const e = new MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true, ...extra });
    el.dispatchEvent(e);
    return e;
  };
  const click = (x: number, y: number) => {
    const d = fire('pointerdown', x, y);
    fire('pointerup', x, y);
    return d;
  };
  return { c, el, host, state, store, log, saves, fire, click };
}

async function drawTrend(t: ReturnType<typeof setup>) {
  t.c.chooseTool('trend');
  t.click(X(10), Y(100));
  t.click(X(20), Y(120));
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe('creating', () => {
  it('two clicks with the trend line tool make a drawing, select it, and remember it for undo and the server', async () => {
    const t = setup();
    await t.c.setSymbol('SBIN');
    await drawTrend(t);
    expect(t.c.list).toHaveLength(1);
    expect(t.c.list[0].tool).toBe('trend');
    expect(t.c.list[0].points).toEqual([{ t: 10_000, p: 100 }, { t: 20_000, p: 120 }]);
    expect(t.c.selected?.id).toBe(t.c.list[0].id);
    expect(t.c.tool).toBeNull(); // back to the cursor
    expect(t.c.canUndo).toBe(true);
    expect(t.log.canUndo).toBe(true);
    await t.store.flush();
    expect(JSON.parse(t.saves[0]).drawings).toHaveLength(1);
  });

  it('a click that places an anchor does not reach the chart', () => {
    const t = setup();
    const parent = vi.fn();
    document.body.addEventListener('pointerdown', parent);
    t.c.chooseTool('trend');
    t.fire('pointerdown', X(10), Y(100));
    expect(parent).not.toHaveBeenCalled();
  });

  it('a click on an axis is left to the chart', () => {
    const t = setup();
    t.c.chooseTool('trend');
    const e = t.fire('pointerdown', 620, 100); // right of the pane: the price axis
    expect(e.defaultPrevented).toBe(false);
    t.click(300, 410); // below the pane: the time axis
    expect(t.c.list).toHaveLength(0);
  });

  it('the magnet snaps a click to the bar prices', async () => {
    const t = setup();
    t.c.setMagnet('strong');
    t.c.chooseTool('hline');
    t.click(X(10), Y(100) - 40); // far above the bar; strong still snaps to its high (120)
    expect(t.c.list[0].points[0]).toEqual({ t: 10_000, p: 120 });
  });

  it('a text drawing starts with the words "Text" and a position tool needs two clicks', () => {
    const t = setup();
    t.c.chooseTool('text');
    t.click(X(5), Y(100));
    expect(t.c.list[0].text).toBe('Text');
    t.c.chooseTool('long');
    t.click(X(10), Y(100));
    expect(t.c.list).toHaveLength(1);
    t.click(X(30), Y(115));
    expect(t.c.list[1].points.map((p) => p.p)).toEqual([100, 85, 115]);
  });

  it('keep drawing leaves the tool chosen after each drawing; Escape ends it', () => {
    const t = setup();
    t.c.setKeepDrawing(true);
    t.c.chooseTool('hline');
    t.click(X(5), Y(100));
    t.click(X(6), Y(110));
    expect(t.c.list).toHaveLength(2);
    expect(t.c.tool).toBe('hline');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(t.c.tool).toBeNull();
  });

  it('new drawings take the colour, width and line style last chosen', async () => {
    const t = setup();
    await drawTrend(t);
    t.c.change({ style: { color: '#f23645', width: 4, dash: 'dashed' } });
    t.c.chooseTool('hline');
    t.click(X(5), Y(100));
    expect(t.c.list[1].style).toMatchObject({ color: '#f23645', width: 4, dash: 'dashed' });
  });
});

describe('selecting and moving', () => {
  it('a press on a drawing selects it and starts a drag that freezes the chart; release commits one step', async () => {
    const t = setup();
    await drawTrend(t);
    t.c.select(null);
    const before = t.c.list;
    t.fire('pointerdown', X(15), Y(110)); // the middle of the line
    expect(t.c.selected?.id).toBe(before[0].id);
    expect(t.state.frozen).toEqual([true]);
    t.fire('pointermove', X(15) + 50, Y(110) - 20); // 5 bars right, 10 up
    t.fire('pointermove', X(15) + 50, Y(110) - 20);
    t.fire('pointerup', X(15) + 50, Y(110) - 20);
    expect(t.state.frozen).toEqual([true, false]);
    expect(t.c.list[0].points).toEqual([{ t: 15_000, p: 110 }, { t: 25_000, p: 130 }]);
    expect(before[0].points[0]).toEqual({ t: 10_000, p: 100 }); // the old list was left alone
    t.c.undoStep();
    expect(t.c.list[0].points[0]).toEqual({ t: 10_000, p: 100 });
    t.c.redoStep();
    expect(t.c.list[0].points[0]).toEqual({ t: 15_000, p: 110 });
  });

  it('dragging a handle moves that anchor only', async () => {
    const t = setup();
    await drawTrend(t);
    t.fire('pointerdown', X(20), Y(120));
    t.fire('pointermove', X(30), Y(140));
    t.fire('pointerup', X(30), Y(140));
    expect(t.c.list[0].points).toEqual([{ t: 10_000, p: 100 }, { t: 30_000, p: 140 }]);
  });

  it('a press without movement changes nothing and adds no history step', async () => {
    const t = setup();
    await drawTrend(t);
    t.c.undoStep();
    t.c.redoStep();
    const steps = t.c.canRedo;
    t.fire('pointerdown', X(15), Y(110));
    t.fire('pointerup', X(15), Y(110));
    expect(t.c.canRedo).toBe(steps);
    expect(t.c.list[0].points[0].t).toBe(10_000);
  });

  it('a locked drawing is selected but does not move, and the chart stays free', async () => {
    const t = setup();
    await drawTrend(t);
    t.c.change({ locked: true });
    t.fire('pointerdown', X(15), Y(110));
    t.fire('pointermove', X(25), Y(130));
    t.fire('pointerup', X(25), Y(130));
    expect(t.c.selected).not.toBeNull();
    expect(t.state.frozen).toEqual([]);
    expect(t.c.list[0].points[0]).toEqual({ t: 10_000, p: 100 });
  });

  it('a press on empty chart deselects and is left to the chart', async () => {
    const t = setup();
    await drawTrend(t);
    const parent = vi.fn();
    document.body.addEventListener('pointerdown', parent);
    const e = t.fire('pointerdown', X(50), Y(30));
    expect(t.c.selected).toBeNull();
    expect(e.defaultPrevented).toBe(false);
    expect(parent).toHaveBeenCalled();
  });

  it('Escape in the middle of a drag puts the drawing back and gives the chart its pan and zoom', async () => {
    const t = setup();
    await drawTrend(t);
    t.fire('pointerdown', X(15), Y(110));
    t.fire('pointermove', X(35), Y(150));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(t.c.list[0].points[0]).toEqual({ t: 10_000, p: 100 });
    expect(t.state.frozen).toEqual([true, false]);
  });
});

describe('editing', () => {
  it('Delete removes the selected drawing, but not a locked one, and not while typing in a box', async () => {
    const t = setup();
    await drawTrend(t);
    const input = document.createElement('input');
    document.body.append(input);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
    expect(t.c.list).toHaveLength(1);
    t.c.change({ locked: true });
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }));
    expect(t.c.list).toHaveLength(1);
    t.c.change({ locked: false });
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }));
    expect(t.c.list).toHaveLength(0);
    expect(t.c.selected).toBeNull();
  });

  it('duplicate, reorder, lock all, hide all and remove all are steps that can be undone', async () => {
    const t = setup();
    await drawTrend(t);
    t.c.duplicateSelected();
    expect(t.c.list).toHaveLength(2);
    const first = t.c.list[0].id;
    t.c.select(first);
    t.c.reorderSelected('front');
    expect(t.c.list[1].id).toBe(first);
    t.c.lockAll(true);
    expect(t.c.list.every((d) => d.locked)).toBe(true);
    t.c.hideAll(true);
    expect(t.c.list.every((d) => d.hidden)).toBe(true);
    t.c.removeEverything();
    expect(t.c.list).toHaveLength(0);
    t.c.undoStep();
    expect(t.c.list).toHaveLength(2);
  });

  it('deleteById refuses a locked drawing', async () => {
    const t = setup();
    await drawTrend(t);
    const id = t.c.list[0].id;
    t.c.change({ locked: true }, id);
    t.c.deleteById(id);
    expect(t.c.list).toHaveLength(1);
  });
});

describe('symbols', () => {
  it('shows what is saved for the symbol, and changing symbol drops a half-drawn tool and starts a new history', async () => {
    const saved = JSON.stringify({ version: 1, symbol: 'TCS', drawings: [newDrawing('hline', [{ t: 5000, p: 90 }])] });
    const t = setup({ saved });
    await t.c.setSymbol('TCS');
    expect(t.c.list).toHaveLength(1);
    expect(t.c.canUndo).toBe(false);
    t.c.chooseTool('trend');
    t.click(X(1), Y(100));
    await t.c.setSymbol('TCS');
    expect(t.c.tool).toBeNull();
  });

  it('a load that finishes after a newer symbol was asked for is ignored', async () => {
    const slow = setup();
    let release: (v: string | null) => void = () => {};
    (slow.store as unknown as { load: (s: string) => Promise<[]> }).load = (s) => (s === 'A' ? new Promise((r) => (release = r as never)) : Promise.resolve([]));
    const first = slow.c.setSymbol('A');
    await slow.c.setSymbol('B');
    release(JSON.stringify([newDrawing('hline', [{ t: 1, p: 1 }])]));
    await first;
    expect(slow.c.list).toEqual([]);
  });
});

describe('painting and switching off', () => {
  const recorder = () => {
    const calls: string[] = [];
    const ctx = new Proxy({}, { get: (_t, k) => (k === 'measureText' ? () => ({ width: 10 }) : (..._a: unknown[]) => void calls.push(String(k))), set: () => true }) as unknown as CanvasRenderingContext2D;
    return { ctx, calls };
  };

  it('paints every drawing and square handles on the selected one', async () => {
    const t = setup();
    await drawTrend(t);
    const r = recorder();
    t.state.paint!(r.ctx, new Projection(times, 1000, { x: (i) => X(i), index: (x) => (x - 5) / 10, y: Y, price: (y) => (400 - y) / 2 }), size);
    expect(r.calls).toContain('stroke');
    expect(r.calls.filter((c) => c === 'rect').length).toBeGreaterThanOrEqual(3); // the clip and two handles
    t.c.select(null);
    const r2 = recorder();
    t.state.paint!(r2.ctx, new Projection(times, 1000, { x: (i) => X(i), index: (x) => (x - 5) / 10, y: Y, price: (y) => (400 - y) / 2 }), size);
    expect(r2.calls.filter((c) => c === 'rect').length).toBe(1); // only the clip
  });

  it('when drawings are switched off nothing is painted and no pointer is taken', async () => {
    let on = true;
    const t = setup({ enabled: () => on });
    await drawTrend(t);
    on = false;
    const r = recorder();
    t.state.paint!(r.ctx, new Projection(times, 1000, { x: (i) => X(i), index: (x) => (x - 5) / 10, y: Y, price: (y) => (400 - y) / 2 }), size);
    expect(r.calls).toEqual([]);
    t.c.chooseTool('hline');
    const e = t.fire('pointerdown', X(5), Y(100));
    expect(e.defaultPrevented).toBe(false);
    expect(t.c.list).toHaveLength(1);
  });

  it('dispose stops listening', async () => {
    const t = setup();
    t.c.dispose();
    t.c.chooseTool('hline');
    t.click(X(5), Y(100));
    expect(t.c.list).toHaveLength(0);
    expect(t.state.paint).toBeNull();
  });
});
