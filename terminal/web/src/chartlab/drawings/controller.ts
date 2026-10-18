import type { Bar } from '../../bars';
import { History } from '../store';
import { Creator } from './creator';
import { handlesOf, pick, shapesOf, type Bounds } from './geometry';
import type { DrawingHost } from './host';
import {
  add, duplicate, moveBy, moveHandle, remove, removeAll, reorder, setAllHidden, setAllLocked, update, type Anchor, type Drawing, type Style, type ToolId,
} from './model';
import { paintHandles, paintShapes } from './painter';
import { snap, type Magnet, type Projection } from './projection';
import type { ActionLog, DrawingStore } from './store';

export interface ControllerOptions {
  /** The element the chart is in: pointer positions are taken relative to it. */
  element: HTMLElement;
  host: DrawingHost;
  store: DrawingStore;
  log: ActionLog;
  /** The bars as the chart shows them (for the magnet). */
  bars: () => Bar[];
  /** False when drawings cannot be placed on this chart (price-move charts have no time axis). */
  enabled: () => boolean;
}

type Drag = { id: string; kind: 'body' | 'handle'; index: number; from: Anchor; base: Drawing[]; moved: boolean };

const PAINT_STYLE = { accent: '#2962ff', fill: '#ffffff' };

/** Places, selects, moves, edits and removes drawings on the chart. It owns the list for the shown symbol, the selection, the chosen tool, the magnet and the undo history of the
 *  drawings; it reads the pointer on the chart element, paints through the host, and keeps the drawings with the store. */
export class DrawingController {
  list: Drawing[] = [];
  selectedId: string | null = null;
  magnet: Magnet = 'off';
  keepDrawing = false;
  private symbol = '';
  private style: Partial<Style> = {};
  private creator = new Creator();
  private history = new History<Drawing[]>([]);
  private drag: Drag | null = null;
  private listeners = new Set<() => void>();
  private token = 0;
  private down = (e: PointerEvent) => this.onDown(e);
  private moveEvent = (e: PointerEvent) => this.onMove(e);
  private up = (e: PointerEvent) => this.onUp(e);
  private key = (e: KeyboardEvent) => this.onKey(e);

  constructor(private readonly o: ControllerOptions) {
    o.element.addEventListener('pointerdown', this.down, true);
    o.element.addEventListener('pointermove', this.moveEvent, true);
    o.element.addEventListener('pointerup', this.up, true);
    o.element.addEventListener('pointercancel', this.up, true);
    document.addEventListener('keydown', this.key);
    o.host.onPaint((ctx, proj, size) => this.paint(ctx, proj, size));
  }

  // ---- state for the interface ----------------------------------------------------------------------------------------------------------------

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    this.listeners.forEach((fn) => fn());
  }

  get tool(): ToolId | null {
    return this.creator.active;
  }

  get selected(): Drawing | null {
    return this.list.find((d) => d.id === this.selectedId) ?? null;
  }

  get canUndo(): boolean {
    return this.history.canUndo;
  }

  get canRedo(): boolean {
    return this.history.canRedo;
  }

  // ---- symbol ---------------------------------------------------------------------------------------------------------------------------------

  /** Show another symbol's drawings. A drag or a half-drawn tool is dropped; the history starts again. */
  async setSymbol(symbol: string): Promise<void> {
    const mine = ++this.token;
    this.symbol = symbol;
    this.cancelAll();
    this.setList([], false);
    this.history.reset([]);
    const list = await this.o.store.load(symbol);
    if (mine !== this.token) return;
    this.list = list;
    this.history.reset(list);
    this.selectedId = null;
    this.o.host.redraw();
    this.emit();
  }

  // ---- changes --------------------------------------------------------------------------------------------------------------------------------

  private setList(list: Drawing[], save = true): void {
    this.list = list;
    if (this.selectedId && !list.some((d) => d.id === this.selectedId)) this.selectedId = null;
    if (save && this.symbol) this.o.store.save(this.symbol, list);
    this.o.host.redraw();
    this.emit();
  }

  /** A change the person made: kept for undo and saved. */
  private commit(list: Drawing[]): void {
    this.history.push(list);
    this.o.log.push('drawings');
    this.setList(list);
  }

  chooseTool(tool: ToolId | null): void {
    this.drag = null;
    if (tool) {
      this.creator.begin(tool);
      this.selectedId = null;
    } else this.creator.cancel();
    this.o.host.redraw();
    this.emit();
  }

  setMagnet(mode: Magnet): void {
    this.magnet = mode;
    this.emit();
  }

  setKeepDrawing(on: boolean): void {
    this.keepDrawing = on;
    this.emit();
  }

  select(id: string | null): void {
    this.selectedId = id && this.list.some((d) => d.id === id) ? id : null;
    this.o.host.redraw();
    this.emit();
  }

  /** Change the selected drawing's look, words or flags. New drawings take the colour, width and line style last chosen. */
  change(patch: Parameters<typeof update>[2], id = this.selectedId): void {
    if (!id) return;
    if (patch.style) {
      const { color, width, dash } = patch.style;
      this.style = { ...this.style, ...(color ? { color } : {}), ...(width ? { width } : {}), ...(dash ? { dash } : {}) };
    }
    this.commit(update(this.list, id, patch));
  }

  deleteSelected(): void {
    const d = this.selected;
    if (d && !d.locked) this.commit(remove(this.list, d.id));
  }

  deleteById(id: string): void {
    const d = this.list.find((x) => x.id === id);
    if (d && !d.locked) this.commit(remove(this.list, id));
  }

  duplicateSelected(): void {
    const d = this.selected;
    const proj = this.o.host.projection();
    if (!d || !proj) return;
    const dt = proj.step * 2;
    const r = duplicate(this.list, d.id, dt, 0);
    if (r.id) {
      this.commit(r.list);
      this.selectedId = r.id;
      this.emit();
    }
  }

  reorderSelected(to: 'front' | 'back'): void {
    if (this.selectedId) this.commit(reorder(this.list, this.selectedId, to));
  }

  lockAll(locked: boolean): void {
    const next = setAllLocked(this.list, locked);
    if (next !== this.list) this.commit(next);
  }

  hideAll(hidden: boolean): void {
    const next = setAllHidden(this.list, hidden);
    if (next !== this.list) this.commit(next);
  }

  removeEverything(): void {
    if (this.list.length) this.commit(removeAll());
  }

  /** Step the drawings back or forward (the view decides, from the action log, whether this is the step to take). */
  undoStep(): boolean {
    const l = this.history.undo();
    if (!l) return false;
    this.setList(l);
    return true;
  }

  redoStep(): boolean {
    const l = this.history.redo();
    if (!l) return false;
    this.setList(l);
    return true;
  }

  // ---- pointer --------------------------------------------------------------------------------------------------------------------------------

  private area(e: PointerEvent): { x: number; y: number; proj: Projection; size: Bounds } | null {
    if (!this.o.enabled()) return null;
    const proj = this.o.host.projection();
    const size = this.o.host.size();
    if (!proj || !size) return null;
    const r = this.o.element.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    if (x < 0 || y < 0 || x > size.w || y > size.h) return null; // on an axis: the chart's own
    return { x, y, proj, size };
  }

  private anchorAt(a: { x: number; y: number; proj: Projection }): Anchor | null {
    return snap(a.proj, this.o.bars(), a.x, a.y, this.magnet);
  }

  private onDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    const a = this.area(e);
    if (!a) return;
    if (this.creator.active) {
      e.preventDefault();
      e.stopPropagation();
      const at = this.anchorAt(a);
      if (!at) return;
      const tool = this.creator.active;
      const done = this.creator.click(at, this.style);
      if (done) {
        if (tool === 'text') done.text = 'Text';
        this.commit(add(this.list, done));
        this.selectedId = done.id;
        if (this.keepDrawing && tool) this.creator.begin(tool);
        this.emit();
      }
      this.o.host.redraw();
      return;
    }
    const found = pick(this.list, a.proj, a.size, a.x, a.y);
    if (!found) {
      if (this.selectedId) this.select(null);
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    this.select(found.id);
    const d = this.selected;
    const at = this.anchorAt(a);
    if (!d || d.locked || !at) return;
    this.drag = { id: d.id, kind: found.hit.kind, index: found.hit.kind === 'handle' ? found.hit.index : -1, from: at, base: this.list, moved: false };
    this.o.host.freeze(true);
    try {
      this.o.element.setPointerCapture?.(e.pointerId);
    } catch {
      /* a synthetic or finished pointer: the drag still works while it stays over the chart */
    }
  }

  private onMove(e: PointerEvent): void {
    const a = this.area(e);
    if (this.drag) {
      e.stopPropagation();
      if (!a) return;
      const at = this.anchorAt(a);
      if (!at) return;
      const { id, kind, index, from, base } = this.drag;
      this.drag.moved = true;
      this.list = kind === 'handle' ? moveHandle(base, id, index, at) : moveBy(base, id, at.t - from.t, at.p - from.p);
      this.o.host.redraw();
      return;
    }
    if (!a) return;
    if (this.creator.active) {
      const at = this.anchorAt(a);
      if (at) this.creator.move(at);
      this.o.host.redraw();
      this.o.element.classList.add('drawing');
      return;
    }
    this.o.element.classList.remove('drawing');
    this.o.element.classList.toggle('over-drawing', pick(this.list, a.proj, a.size, a.x, a.y) !== null);
  }

  private onUp(e: PointerEvent): void {
    const drag = this.drag;
    if (!drag) return;
    e.stopPropagation();
    this.drag = null;
    this.o.host.freeze(false);
    try {
      this.o.element.releasePointerCapture?.(e.pointerId);
    } catch {
      /* already released */
    }
    if (drag.moved && this.list !== drag.base) {
      const moved = this.list;
      this.list = drag.base; // commit pushes the new list on top of the old one in the history
      this.commit(moved);
    }
  }

  private onKey(e: KeyboardEvent): void {
    if (!this.o.enabled()) return;
    const t = e.target as HTMLElement | null;
    if (t && ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return;
    if (e.key === 'Escape') {
      if (this.creator.active) this.chooseTool(null);
      else if (this.selectedId) this.select(null);
      this.cancelAll();
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && this.selectedId) {
      e.preventDefault();
      this.deleteSelected();
    }
  }

  private cancelAll(): void {
    if (this.drag) {
      this.list = this.drag.base;
      this.drag = null;
      this.o.host.freeze(false);
    }
    this.creator.cancel();
    this.o.element.classList.remove('drawing');
  }

  // ---- painting -------------------------------------------------------------------------------------------------------------------------------

  private paint(ctx: CanvasRenderingContext2D, proj: Projection, size: Bounds): void {
    if (!this.o.enabled()) return;
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, size.w, size.h);
    ctx.clip();
    for (const d of this.list) paintShapes(ctx, shapesOf(d, proj, size));
    const preview = this.creator.preview(this.style);
    if (preview) {
      ctx.globalAlpha = 0.75;
      paintShapes(ctx, shapesOf(preview, proj, size));
      ctx.globalAlpha = 1;
    }
    const sel = this.selected;
    if (sel && !sel.hidden) paintHandles(ctx, handlesOf(sel, proj), PAINT_STYLE.accent, PAINT_STYLE.fill);
    ctx.restore();
  }

  dispose(): void {
    this.o.element.removeEventListener('pointerdown', this.down, true);
    this.o.element.removeEventListener('pointermove', this.moveEvent, true);
    this.o.element.removeEventListener('pointerup', this.up, true);
    this.o.element.removeEventListener('pointercancel', this.up, true);
    document.removeEventListener('keydown', this.key);
    this.o.host.onPaint(null);
    this.listeners.clear();
  }
}

