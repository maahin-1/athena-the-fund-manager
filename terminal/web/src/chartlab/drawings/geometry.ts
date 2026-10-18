/** What each drawing looks like in pixels, and which of its parts a pointer is on. Pure: the painter and the tests both read these shapes. */
import { pct, price, signed } from '../../format';
import type { Dash, Drawing } from './model';
import type { Projection } from './projection';

export interface Bounds { w: number; h: number }
export type Pt = [number, number];
interface Paint { color: string; width: number; dash: Dash }

export type Shape =
  | ({ k: 'line'; x1: number; y1: number; x2: number; y2: number } & Paint)
  | ({ k: 'poly'; pts: Pt[]; closed: boolean; fill?: string } & Paint)
  | ({ k: 'rect'; x: number; y: number; w: number; h: number; fill?: string } & Paint)
  | ({ k: 'ellipse'; cx: number; cy: number; rx: number; ry: number; fill?: string } & Paint)
  | { k: 'text'; x: number; y: number; text: string; color: string; align: 'left' | 'center' | 'right'; bg?: string; size: number };

export type Hit = { kind: 'handle'; index: number } | { kind: 'body' };

export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export const FIB_EXT_RATIOS = [0, 0.618, 1, 1.272, 1.618, 2.618];
const FIB_COLORS: Record<number, string> = {
  0: '#787b86', 0.236: '#f23645', 0.382: '#ff9800', 0.5: '#089981', 0.618: '#00bcd4', 0.786: '#2962ff', 1: '#787b86', 1.272: '#9c27b0', 1.618: '#f23645', 2.618: '#ff9800',
};
const UP = '#089981';
const DOWN = '#f23645';
const FILL_ALPHA = 0.14;

/** `#rrggbb` with an alpha. */
export function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// ---- numbers behind the tools -------------------------------------------------------------------------------------------------------------------

/** Fibonacci levels of a retracement (two anchors A, B: ratio 0 at B and 1 at A) or an extension (A, B, C: C plus the A to B move times the ratio). */
export function fibLevels(d: Drawing): { ratio: number; price: number }[] {
  const [A, B, C] = d.points;
  if (d.tool === 'fib') return FIB_RATIOS.map((ratio) => ({ ratio, price: B.p + (A.p - B.p) * ratio }));
  if (d.tool === 'fibext' && C) return FIB_EXT_RATIOS.map((ratio) => ({ ratio, price: C.p + (B.p - A.p) * ratio }));
  return [];
}

export interface Measure { dp: number; pct: number; bars: number; seconds: number }

/** The change between two anchors: price, percent of the first price, bars between them (as the chart shows them) and time. */
export function measureOf(d: Drawing, proj: Projection): Measure {
  const [a, b] = d.points;
  return { dp: b.p - a.p, pct: a.p ? ((b.p - a.p) / a.p) * 100 : 0, bars: Math.round(Math.abs(proj.indexOf(b.t) - proj.indexOf(a.t))), seconds: Math.abs(b.t - a.t) };
}

export interface Position { entry: number; stop: number; target: number; risk: number; reward: number; ratio: number | null; targetPct: number; stopPct: number; long: boolean }

/** Risk and reward of a position tool: anchors are entry, stop and target (the stop and target share a time). */
export function positionOf(d: Drawing): Position {
  const entry = d.points[0].p;
  const stop = d.points[1].p;
  const target = d.points[2].p;
  const risk = Math.abs(entry - stop);
  const reward = Math.abs(target - entry);
  return { entry, stop, target, risk, reward, ratio: risk > 0 ? reward / risk : null, targetPct: entry ? ((target - entry) / entry) * 100 : 0, stopPct: entry ? ((stop - entry) / entry) * 100 : 0, long: target >= entry };
}

export function spanText(seconds: number): string {
  if (seconds >= 86_400) return `${Math.round(seconds / 86_400)}d`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h}h${m ? ` ${m}m` : ''}` : `${m}m`;
}

// ---- line clipping -------------------------------------------------------------------------------------------------------------------------

/** The part of the line through (x1,y1) and (x2,y2) that is in the pane: the segment only, or continued past either end, or both (Liang-Barsky). Null when nothing is in view. */
function clipLine(x1: number, y1: number, x2: number, y2: number, b: Bounds, left: boolean, right: boolean): [number, number, number, number] | null {
  const dx = x2 - x1;
  const dy = y2 - y1;
  let lo = left ? -Infinity : 0;
  let hi = right ? Infinity : 1;
  const edge = (p: number, q: number): boolean => {
    if (p === 0) return q >= 0;
    const r = q / p;
    if (p < 0) lo = Math.max(lo, r);
    else hi = Math.min(hi, r);
    return lo <= hi;
  };
  if (!(edge(-dx, x1) && edge(dx, b.w - x1) && edge(-dy, y1) && edge(dy, b.h - y1))) return null;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null; // no direction (both ends the same point) and an extension asked for
  return [x1 + lo * dx, y1 + lo * dy, x1 + hi * dx, y1 + hi * dy];
}

// ---- shapes --------------------------------------------------------------------------------------------------------------------------------------

function pixels(d: Drawing, proj: Projection): Pt[] | null {
  const out: Pt[] = [];
  for (const a of d.points) {
    const x = proj.x(a.t);
    const y = proj.y(a.p);
    if (x === null || y === null) return null;
    out.push([x, y]);
  }
  return out;
}

const fmt = (n: number): string => price(n);

/** The pixel position of each anchor (where its handle sits); empty when the chart cannot place them. */
export function handlesOf(d: Drawing, proj: Projection): Pt[] {
  return pixels(d, proj) ?? [];
}

export function shapesOf(d: Drawing, proj: Projection, bounds: Bounds): Shape[] {
  const p = pixels(d, proj);
  if (!p || d.hidden) return [];
  const { color, width, dash } = d.style;
  const paint: Paint = { color, width, dash };
  const out: Shape[] = [];
  const line = (a: Pt, b: Pt, left: boolean, right: boolean, over: Partial<Paint> = {}): Shape | null => {
    const c = clipLine(a[0], a[1], b[0], b[1], bounds, left, right);
    return c ? { k: 'line', x1: c[0], y1: c[1], x2: c[2], y2: c[3], ...paint, ...over } : null;
  };
  const push = (s: Shape | null) => s && out.push(s);
  const text = (x: number, y: number, t: string, align: 'left' | 'center' | 'right' = 'left', bg?: string, textColor = color) =>
    out.push({ k: 'text', x, y, text: t, color: textColor, align, ...(bg ? { bg } : {}), size: 11 });
  const extL = d.style.extendLeft === true;
  const extR = d.style.extendRight === true;
  const [a, b, c] = p;

  switch (d.tool) {
    case 'trend':
      push(line(a, b, extL, extR));
      break;
    case 'ray':
      push(line(a, b, false, true));
      break;
    case 'extended':
      push(line(a, b, true, true));
      break;
    case 'info': {
      push(line(a, b, extL, extR));
      const m = measureOf(d, proj);
      text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 - 8, `${signed(m.dp)} (${pct(m.pct)}) · ${m.bars} bars`, 'center', rgba('#000000', 0.45), '#ffffff');
      break;
    }
    case 'hline':
      push(line([0, a[1]], [bounds.w, a[1]], false, false));
      text(bounds.w - 4, a[1] - 3, fmt(d.points[0].p), 'right');
      break;
    case 'hray':
      push(line(a, [a[0] + 1, a[1]], false, true));
      text(bounds.w - 4, a[1] - 3, fmt(d.points[0].p), 'right');
      break;
    case 'vline':
      push(line([a[0], 0], [a[0], bounds.h], false, false));
      break;
    case 'cross':
      push(line([0, a[1]], [bounds.w, a[1]], false, false));
      push(line([a[0], 0], [a[0], bounds.h], false, false));
      break;
    case 'channel': {
      push(line(a, b, extL, extR));
      const off = channelOffset(a, b, c);
      const a2: Pt = [a[0], a[1] + off];
      const b2: Pt = [b[0], b[1] + off];
      push(line(a2, b2, extL, extR));
      push(line([a[0], a[1] + off / 2], [b[0], b[1] + off / 2], extL, extR, { dash: 'dashed', width: 1 }));
      out.unshift({ k: 'poly', pts: [a, b, b2, a2], closed: true, fill: rgba(color, FILL_ALPHA), color, width: 0, dash: 'solid' });
      break;
    }
    case 'flat': {
      push(line(a, b, extL, extR));
      push(line([a[0], c[1]], [b[0], c[1]], extL, extR));
      out.unshift({ k: 'poly', pts: [a, b, [b[0], c[1]], [a[0], c[1]]], closed: true, fill: rgba(color, FILL_ALPHA), color, width: 0, dash: 'solid' });
      break;
    }
    case 'fib': {
      const left = Math.min(a[0], b[0]);
      const right = Math.max(a[0], b[0]);
      const levels = fibLevels(d);
      const ys = levels.map((l) => proj.y(l.price) as number);
      levels.forEach((l, i) => {
        if (i > 0) out.push({ k: 'rect', x: left, y: Math.min(ys[i - 1], ys[i]), w: (extR ? bounds.w : right) - left, h: Math.abs(ys[i] - ys[i - 1]), fill: rgba(FIB_COLORS[l.ratio] ?? color, 0.1), color, width: 0, dash: 'solid' });
      });
      levels.forEach((l, i) => {
        push(line([left, ys[i]], [right, ys[i]], false, extR, { color: FIB_COLORS[l.ratio] ?? color, width: 1 }));
        text(left + 3, ys[i] - 3, `${l.ratio} (${fmt(l.price)})`, 'left', undefined, FIB_COLORS[l.ratio] ?? color);
      });
      push(line(a, b, false, false, { dash: 'dashed', width: 1 }));
      break;
    }
    case 'fibext': {
      const left = Math.min(a[0], b[0], c[0]);
      const right = Math.max(a[0], b[0], c[0]);
      const levels = fibLevels(d);
      levels.forEach((l) => {
        const y = proj.y(l.price) as number;
        push(line([left, y], [right, y], false, extR, { color: FIB_COLORS[l.ratio] ?? color, width: 1 }));
        text(left + 3, y - 3, `${l.ratio} (${fmt(l.price)})`, 'left', undefined, FIB_COLORS[l.ratio] ?? color);
      });
      push(line(a, b, false, false, { dash: 'dashed', width: 1 }));
      push(line(b, c, false, false, { dash: 'dashed', width: 1 }));
      break;
    }
    case 'rect':
      out.push({ k: 'rect', x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]), fill: rgba(color, FILL_ALPHA), ...paint });
      break;
    case 'ellipse':
      out.push({ k: 'ellipse', cx: (a[0] + b[0]) / 2, cy: (a[1] + b[1]) / 2, rx: Math.abs(b[0] - a[0]) / 2, ry: Math.abs(b[1] - a[1]) / 2, fill: rgba(color, FILL_ALPHA), ...paint });
      break;
    case 'triangle':
      out.push({ k: 'poly', pts: [a, b, c], closed: true, fill: rgba(color, FILL_ALPHA), ...paint });
      break;
    case 'text':
      text(a[0], a[1], d.text || 'Text', 'left');
      break;
    case 'label':
      text(a[0] + 6, a[1], d.text || fmt(d.points[0].p), 'left', rgba(color, 0.9), '#ffffff');
      out.push({ k: 'ellipse', cx: a[0], cy: a[1], rx: 3, ry: 3, fill: color, color, width: 1, dash: 'solid' });
      break;
    case 'measure': {
      const m = measureOf(d, proj);
      const up = m.dp >= 0;
      const tone = up ? UP : DOWN;
      out.push({ k: 'rect', x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]), fill: rgba(tone, FILL_ALPHA), color: tone, width: 1, dash: 'dashed' });
      push(line(a, b, false, false, { color: tone, width: 1 }));
      text((a[0] + b[0]) / 2, Math.min(a[1], b[1]) - 6, `${signed(m.dp)} (${pct(m.pct)})  ${m.bars} bars, ${spanText(m.seconds)}`, 'center', rgba(tone, 0.9), '#ffffff');
      break;
    }
    case 'pricerange': {
      const m = measureOf(d, proj);
      const tone = m.dp >= 0 ? UP : DOWN;
      const left = Math.min(a[0], b[0]);
      const w = Math.max(Math.abs(b[0] - a[0]), 1);
      out.push({ k: 'rect', x: left, y: Math.min(a[1], b[1]), w, h: Math.abs(b[1] - a[1]), fill: rgba(tone, FILL_ALPHA), color: tone, width: 1, dash: 'solid' });
      const mid = left + w / 2;
      push(line([mid, a[1]], [mid, b[1]], false, false, { color: tone, width: 1 }));
      text(mid, Math.min(a[1], b[1]) - 6, `${signed(m.dp)} (${pct(m.pct)})`, 'center', rgba(tone, 0.9), '#ffffff');
      break;
    }
    case 'daterange': {
      const m = measureOf(d, proj);
      const left = Math.min(a[0], b[0]);
      const right = Math.max(a[0], b[0]);
      out.push({ k: 'rect', x: left, y: 0, w: Math.max(right - left, 1), h: bounds.h, fill: rgba(color, 0.08), color, width: 1, dash: 'dashed' });
      const y = bounds.h / 2;
      push(line([left, y], [right, y], false, false, { width: 1 }));
      text((left + right) / 2, y - 6, `${m.bars} bars, ${spanText(m.seconds)}`, 'center', rgba(color, 0.9), '#ffffff');
      break;
    }
    case 'long':
    case 'short': {
      const pos = positionOf(d);
      const left = Math.min(a[0], b[0], c[0]);
      const right = Math.max(a[0], b[0], c[0]);
      const w = Math.max(right - left, 1);
      const yEntry = a[1];
      out.push({ k: 'rect', x: left, y: Math.min(yEntry, c[1]), w, h: Math.abs(c[1] - yEntry), fill: rgba(UP, 0.18), color: UP, width: 1, dash: 'solid' });
      out.push({ k: 'rect', x: left, y: Math.min(yEntry, b[1]), w, h: Math.abs(b[1] - yEntry), fill: rgba(DOWN, 0.18), color: DOWN, width: 1, dash: 'solid' });
      push(line([left, yEntry], [right, yEntry], false, false, { color: '#787b86', width: 1 }));
      text(left + w / 2, c[1] - (pos.target >= pos.entry ? 5 : -13), `Target ${fmt(pos.target)} (${pct(pos.targetPct)})`, 'center', undefined, UP);
      text(left + w / 2, b[1] - (pos.stop >= pos.entry ? 5 : -13), `Stop ${fmt(pos.stop)} (${pct(pos.stopPct)})`, 'center', undefined, DOWN);
      text(left + w / 2, yEntry - 4, `${d.tool === 'long' ? 'Long' : 'Short'}  R:R ${pos.ratio === null ? '–' : pos.ratio.toFixed(1)}`, 'center', rgba('#000000', 0.5), '#ffffff');
      break;
    }
  }
  return out;
}

/** A channel's third anchor sets how far the second line sits from the first: the vertical gap between that point and the base line at its x. */
function channelOffset(a: Pt, b: Pt, c: Pt): number {
  const dx = b[0] - a[0];
  const lineY = dx === 0 ? a[1] : a[1] + ((b[1] - a[1]) * (c[0] - a[0])) / dx;
  return c[1] - lineY;
}

// ---- hit-testing ---------------------------------------------------------------------------------------------------------------------------------

function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function inPolygon(px: number, py: number, pts: Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function onShape(s: Shape, x: number, y: number, tol: number): boolean {
  switch (s.k) {
    case 'line':
      return s.width > 0 && distToSegment(x, y, s.x1, s.y1, s.x2, s.y2) <= tol;
    case 'rect': {
      if (s.fill && x >= s.x && x <= s.x + s.w && y >= s.y && y <= s.y + s.h) return true;
      const edges: [number, number, number, number][] = [[s.x, s.y, s.x + s.w, s.y], [s.x + s.w, s.y, s.x + s.w, s.y + s.h], [s.x + s.w, s.y + s.h, s.x, s.y + s.h], [s.x, s.y + s.h, s.x, s.y]];
      return s.width > 0 && edges.some((e) => distToSegment(x, y, ...e) <= tol);
    }
    case 'ellipse': {
      const rx = Math.max(s.rx, 0.5);
      const ry = Math.max(s.ry, 0.5);
      const q = ((x - s.cx) / rx) ** 2 + ((y - s.cy) / ry) ** 2;
      if (s.fill && q <= 1) return true;
      return Math.abs(Math.sqrt(q) - 1) * Math.min(rx, ry) <= tol;
    }
    case 'poly': {
      if (s.fill && inPolygon(x, y, s.pts)) return true;
      const n = s.pts.length;
      for (let i = 0; i < (s.closed ? n : n - 1); i++) {
        const [x1, y1] = s.pts[i];
        const [x2, y2] = s.pts[(i + 1) % n];
        if (s.width > 0 && distToSegment(x, y, x1, y1, x2, y2) <= tol) return true;
      }
      return false;
    }
    case 'text': {
      const w = s.text.length * 6.6 + 8;
      const left = s.align === 'left' ? s.x : s.align === 'right' ? s.x - w : s.x - w / 2;
      return x >= left && x <= left + w && y >= s.y - 13 && y <= s.y + 3;
    }
  }
}

/** What a pointer at (x, y) is on: one of the drawing's handles (they win), its body, or nothing. */
export function hit(d: Drawing, proj: Projection, bounds: Bounds, x: number, y: number, tol = 6): Hit | null {
  if (d.hidden) return null;
  const handles = handlesOf(d, proj);
  for (let i = 0; i < handles.length; i++) if (Math.hypot(handles[i][0] - x, handles[i][1] - y) <= tol + 2) return { kind: 'handle', index: i };
  return shapesOf(d, proj, bounds).some((s) => onShape(s, x, y, tol)) ? { kind: 'body' } : null;
}

/** The topmost drawing under the pointer (the last in the list wins). */
export function pick(list: Drawing[], proj: Projection, bounds: Bounds, x: number, y: number, tol = 6): { id: string; hit: Hit } | null {
  for (let i = list.length - 1; i >= 0; i--) {
    const h = hit(list[i], proj, bounds, x, y, tol);
    if (h) return { id: list[i].id, hit: h };
  }
  return null;
}
