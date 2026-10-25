/** The later drawing tools (tier 2 and tier 3): more lines and channels, pitchforks, Fibonacci fans, circles, arcs and time zones, Gann, markers, forecast, harmonic and chart patterns,
 *  Elliott waves and cycles. Each is drawn from its anchors in pixels, the same way the first tools are. A pattern tool draws what the person placed and the ratios between the legs,
 *  measured from the anchor prices; it does not decide whether a pattern is valid and it predicts nothing. */
import { pct, price, signed } from '../../format';
import { DOWN, FIB_COLORS, FILL_ALPHA, rgba, UP, type Bounds, type Paint, type Pt, type Shape } from './draw-kit';
import type { Drawing } from './model';
import type { Projection } from './projection';

export interface Kit {
  d: Drawing;
  p: Pt[];
  proj: Projection;
  bounds: Bounds;
  out: Shape[];
  /** The line through two pixel points, clipped to the pane, or null when none of it is in view. */
  line(a: Pt, b: Pt, left: boolean, right: boolean, over?: Partial<Paint>): Shape | null;
  text(x: number, y: number, t: string, align?: 'left' | 'center' | 'right', bg?: string, color?: string): void;
  measure(d: Drawing): { dp: number; pct: number; bars: number; seconds: number };
  span(seconds: number): string;
}

const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const gap = (a: Pt, b: Pt): number => Math.hypot(b[0] - a[0], b[1] - a[1]);
const ratioText = (n: number): string => (Number.isFinite(n) ? n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') : '–');
const fibColor = (ratio: number, fallback: string): string => FIB_COLORS[ratio] ?? fallback;

export const FIB_FAN_RATIOS = [0.25, 0.382, 0.5, 0.618, 0.75, 1];
export const FIB_CHANNEL_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618, 4.236];
export const FIB_TIME_ZONES = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89];
export const FIB_CIRCLE_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618];
export const FIB_ARC_RATIOS = [0.382, 0.5, 0.618, 1];
export const GANN_ANGLES: { rate: number; label: string }[] = [
  { rate: 8, label: '8x1' }, { rate: 4, label: '4x1' }, { rate: 3, label: '3x1' }, { rate: 2, label: '2x1' }, { rate: 1, label: '1x1' },
  { rate: 1 / 2, label: '1x2' }, { rate: 1 / 3, label: '1x3' }, { rate: 1 / 4, label: '1x4' }, { rate: 1 / 8, label: '1x8' },
];
export const GANN_BOX_LEVELS = [0.25, 0.382, 0.5, 0.618, 0.75];
const CYCLES_EITHER_SIDE = 100;

/** The start of a pitchfork's median line: the first pivot, or (Schiff) that pivot moved to halfway between the first two in price, or (modified Schiff) in time and price. */
export function pitchforkOrigin(tool: 'pitchfork' | 'schiff' | 'mschiff', p0: Pt, p1: Pt): Pt {
  if (tool === 'schiff') return [p0[0], (p0[1] + p1[1]) / 2];
  if (tool === 'mschiff') return mid(p0, p1);
  return p0;
}

/** The vertical gap between a point and the line through two others, at the point's x (the third anchor of a channel sets how far its second line sits). */
function channelOffset(a: Pt, b: Pt, c: Pt): number {
  const dx = b[0] - a[0];
  const lineY = dx === 0 ? a[1] : a[1] + ((b[1] - a[1]) * (c[0] - a[0])) / dx;
  return c[1] - lineY;
}

/** The three corners of an arrow head at `tip`, for a line that comes from `from`. */
function head(tip: Pt, from: Pt, size: number): Pt[] {
  const angle = Math.atan2(tip[1] - from[1], tip[0] - from[0]);
  return [tip, [tip[0] - size * Math.cos(angle - 0.45), tip[1] - size * Math.sin(angle - 0.45)], [tip[0] - size * Math.cos(angle + 0.45), tip[1] - size * Math.sin(angle + 0.45)]];
}

/** The ratios between the legs of a pattern, from the anchor prices: leg `to` against leg `from` as |move to| / |move from|, null when the first leg is flat. */
export function legRatio(prices: number[], from: [number, number], to: [number, number]): number | null {
  const base = Math.abs(prices[from[1]] - prices[from[0]]);
  return base > 0 ? Math.abs(prices[to[1]] - prices[to[0]]) / base : null;
}

export function extraShapes(k: Kit): void {
  const { d, p, proj, bounds, out } = k;
  const { color, width, dash } = d.style;
  const paint: Paint = { color, width, dash };
  const prices = d.points.map((a) => a.p);
  const extL = d.style.extendLeft === true;
  const extR = d.style.extendRight === true;
  const push = (s: Shape | null): void => {
    if (s) out.push(s);
  };
  const thin = (a: Pt, b: Pt, over: Partial<Paint> = {}): void => push(k.line(a, b, false, false, { width: 1, dash: 'dashed', ...over }));
  const poly = (pts: Pt[], closed = false, fill?: string, over: Partial<Paint> = {}): void => {
    out.push({ k: 'poly', pts, closed, ...(fill ? { fill } : {}), ...paint, ...over });
  };
  const note = (x: number, y: number, t: string, tone = color): void => k.text(x, y, t, 'center', undefined, tone);
  /** A label beside anchor i: above when the point is a local high on the screen, below otherwise. */
  const letter = (i: number, t: string): void => {
    const here = p[i][1];
    const before = i > 0 ? p[i - 1][1] : null;
    const after = i < p.length - 1 ? p[i + 1][1] : null;
    const high = (before === null || here <= before) && (after === null || here <= after);
    note(p[i][0], here + (high ? -8 : 15), t);
  };
  const legLabel = (a: number, b: number, from: [number, number], to: [number, number]): void => {
    const r = legRatio(prices, from, to);
    const m = mid(p[a], p[b]);
    if (r !== null) k.text(m[0], m[1] - 4, ratioText(r), 'center', rgba('#000000', 0.45), '#ffffff');
  };
  const fill = rgba(color, FILL_ALPHA);
  const [a, b, c, e] = p;

  switch (d.tool) {
    case 'angle': {
      push(k.line(a, b, extL, extR));
      thin(a, [a[0] + Math.max(40, Math.abs(b[0] - a[0])), a[1]], { dash: 'dotted' });
      const degrees = (Math.atan2(a[1] - b[1], b[0] - a[0]) * 180) / Math.PI;
      k.text(a[0] + 6, a[1] - 5, `${degrees.toFixed(1)}°`, 'left', rgba('#000000', 0.45), '#ffffff');
      break;
    }
    case 'arrow':
      push(k.line(a, b, false, false));
      if (gap(a, b) > 0) poly(head(b, a, 9 + width * 2), true, color, { width: 1, dash: 'solid' });
      break;
    case 'disjoint':
      poly([a, b, e, c], true, fill, { width: 0, dash: 'solid' });
      push(k.line(a, b, extL, extR));
      push(k.line(c, e, extL, extR));
      break;
    case 'pitchfork':
    case 'schiff':
    case 'mschiff': {
      const origin = pitchforkOrigin(d.tool, a, b);
      const m = mid(b, c);
      const dir: Pt = [m[0] - origin[0], m[1] - origin[1]];
      const len = Math.hypot(dir[0], dir[1]);
      thin(b, c);
      if (len < 1e-6) break;
      const far = ((bounds.w + bounds.h) * 4) / len;
      const through = (from: Pt): Pt => [from[0] + dir[0], from[1] + dir[1]];
      out.push({ k: 'poly', pts: [b, [b[0] + dir[0] * far, b[1] + dir[1] * far], [c[0] + dir[0] * far, c[1] + dir[1] * far], c], closed: true, fill, color, width: 0, dash: 'solid' });
      push(k.line(origin, m, false, true));
      push(k.line(b, through(b), false, true));
      push(k.line(c, through(c), false, true));
      for (const side of [b, c]) {
        const half = mid(m, side);
        push(k.line(half, through(half), false, true, { width: 1, dash: 'dotted' }));
      }
      thin(a, b, { dash: 'dotted' });
      if (d.tool !== 'pitchfork') thin(origin, a, { dash: 'dotted' });
      break;
    }
    case 'fibchannel': {
      const off = channelOffset(a, b, c);
      const edge = (r: number): [Pt, Pt] => [[a[0], a[1] + off * r], [b[0], b[1] + off * r]];
      const [i0, i1] = edge(0);
      const [j0, j1] = edge(1);
      out.push({ k: 'poly', pts: [i0, i1, j1, j0], closed: true, fill, color, width: 0, dash: 'solid' });
      for (const r of FIB_CHANNEL_RATIOS) {
        const [from, to] = edge(r);
        const tone = fibColor(r, color);
        push(k.line(from, to, extL, extR, { color: tone, width: 1 }));
        k.text(to[0] + 3, to[1] - 3, ratioText(r), 'left', undefined, tone);
      }
      break;
    }
    case 'fibtime': {
      const ia = proj.indexOf(d.points[0].t);
      const unit = proj.indexOf(d.points[1].t) - ia;
      if (unit === 0) {
        thin(a, b);
        break;
      }
      for (const zone of FIB_TIME_ZONES) {
        const x = proj.x(proj.timeAt(ia + unit * zone));
        if (x === null || x < -20 || x > bounds.w + 20) continue;
        push(k.line([x, 0], [x, bounds.h], false, false, { width: 1 }));
        k.text(x + 3, 12, String(zone), 'left', undefined, color);
      }
      break;
    }
    case 'fibfan': {
      thin(a, b);
      for (const r of FIB_FAN_RATIOS) {
        const tone = fibColor(r, color);
        const t1: Pt = [b[0], a[1] + (b[1] - a[1]) * r];
        push(k.line(a, t1, false, true, { color: tone, width: 1, dash: 'solid' }));
        k.text(t1[0] + 3, t1[1] - 3, ratioText(r), 'left', undefined, tone);
        if (r < 1) {
          const t2: Pt = [a[0] + (b[0] - a[0]) * r, b[1]];
          push(k.line(a, t2, false, true, { color: tone, width: 1, dash: 'solid' }));
          k.text(t2[0], t2[1] - 3, ratioText(r), 'center', undefined, tone);
        }
      }
      break;
    }
    case 'fibcircles': {
      const centre = mid(a, b);
      const radius = gap(a, b) / 2;
      thin(a, b);
      for (const r of FIB_CIRCLE_RATIOS) {
        const tone = fibColor(r, color);
        out.push({ k: 'ellipse', cx: centre[0], cy: centre[1], rx: radius * r, ry: radius * r, color: tone, width: 1, dash: 'solid' });
        k.text(centre[0], centre[1] - radius * r - 2, ratioText(r), 'center', undefined, tone);
      }
      break;
    }
    case 'fibarcs': {
      const radius = gap(a, b);
      const base = Math.atan2(a[1] - b[1], a[0] - b[0]);
      thin(a, b);
      for (const r of FIB_ARC_RATIOS) {
        const tone = fibColor(r, color);
        const pts: Pt[] = [];
        for (let i = 0; i <= 36; i++) {
          const angle = base - Math.PI / 2 + (Math.PI * i) / 36;
          pts.push([b[0] + radius * r * Math.cos(angle), b[1] + radius * r * Math.sin(angle)]);
        }
        poly(pts, false, undefined, { color: tone, width: 1, dash: 'solid' });
        k.text(b[0] + radius * r * Math.cos(base), b[1] + radius * r * Math.sin(base) - 3, ratioText(r), 'center', undefined, tone);
      }
      break;
    }
    case 'gannfan': {
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      if (dx === 0) break;
      for (const g of GANN_ANGLES) {
        const end: Pt = [a[0] + dx, a[1] + dy * g.rate];
        const main = g.rate === 1;
        push(k.line(a, end, false, true, { width: main ? Math.max(width, 2) : 1, dash: main ? dash : 'solid' }));
        k.text(end[0] + 3, end[1] - 3, g.label, 'left', undefined, color);
      }
      break;
    }
    case 'gannbox': {
      const left = Math.min(a[0], b[0]);
      const top = Math.min(a[1], b[1]);
      const w = Math.abs(b[0] - a[0]);
      const h = Math.abs(b[1] - a[1]);
      out.push({ k: 'rect', x: left, y: top, w, h, fill, ...paint });
      for (const r of GANN_BOX_LEVELS) {
        push(k.line([left, top + h * r], [left + w, top + h * r], false, false, { width: 1, dash: 'dotted' }));
        push(k.line([left + w * r, top], [left + w * r, top + h], false, false, { width: 1, dash: 'dotted' }));
      }
      push(k.line(a, b, false, false, { width: Math.max(width, 2) }));
      push(k.line([a[0], b[1]], [b[0], a[1]], false, false, { width: 1 }));
      break;
    }
    case 'circle': {
      const radius = gap(a, b);
      out.push({ k: 'ellipse', cx: a[0], cy: a[1], rx: radius, ry: radius, fill, ...paint });
      break;
    }
    case 'arrowup':
    case 'arrowdown': {
      const s = d.tool === 'arrowup' ? 1 : -1; // the tip is at the anchor; the body lies below an up arrow and above a down arrow
      const [x, y] = a;
      poly([[x, y], [x + 8, y + 11 * s], [x + 3, y + 11 * s], [x + 3, y + 24 * s], [x - 3, y + 24 * s], [x - 3, y + 11 * s], [x - 8, y + 11 * s]], true, color, { width: 1, dash: 'solid' });
      if (d.text) k.text(x, y + (s === 1 ? 38 : -30), d.text, 'center', undefined, color);
      break;
    }
    case 'forecast': {
      const m = k.measure(d);
      const tone = m.dp >= 0 ? UP : DOWN;
      out.push({ k: 'rect', x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]), fill: rgba(tone, 0.1), color: tone, width: 1, dash: 'dashed' });
      push(k.line(a, b, false, false, { color: tone }));
      if (gap(a, b) > 0) poly(head(b, a, 10), true, tone, { color: tone, width: 1, dash: 'solid' });
      k.text((a[0] + b[0]) / 2, Math.min(a[1], b[1]) - 6, `${signed(m.dp)} (${pct(m.pct)})  ${m.bars} bars, ${k.span(m.seconds)} · to ${price(d.points[1].p)}`, 'center', rgba(tone, 0.9), '#ffffff');
      break;
    }
    case 'abcd':
      poly(p);
      thin(a, c);
      thin(b, e);
      ['A', 'B', 'C', 'D'].forEach((name, i) => letter(i, name));
      legLabel(1, 2, [0, 1], [1, 2]);
      legLabel(2, 3, [1, 2], [2, 3]);
      break;
    case 'xabcd':
      poly([a, b, c], true, fill, { width: 0, dash: 'solid' });
      poly([c, e, p[4]], true, fill, { width: 0, dash: 'solid' });
      poly(p);
      thin(a, c);
      thin(b, e);
      thin(p[0], p[4]);
      ['X', 'A', 'B', 'C', 'D'].forEach((name, i) => letter(i, name));
      legLabel(1, 2, [0, 1], [1, 2]);
      legLabel(2, 3, [1, 2], [2, 3]);
      legLabel(0, 4, [0, 1], [1, 4]);
      break;
    case 'cypher':
      poly([a, b, c], true, fill, { width: 0, dash: 'solid' });
      poly([c, e, p[4]], true, fill, { width: 0, dash: 'solid' });
      poly(p);
      thin(p[0], p[3]);
      thin(c, p[4]);
      ['X', 'A', 'B', 'C', 'D'].forEach((name, i) => letter(i, name));
      legLabel(1, 2, [0, 1], [1, 2]);
      legLabel(0, 3, [0, 1], [0, 3]);
      legLabel(3, 4, [0, 3], [3, 4]);
      break;
    case 'headshoulders': {
      poly([p[2], p[3], p[4]], true, fill, { width: 0, dash: 'solid' });
      poly(p);
      push(k.line(p[2], p[4], false, true, { dash: 'dashed', width: 1 }));
      letter(1, 'Left shoulder');
      letter(3, 'Head');
      letter(5, 'Right shoulder');
      break;
    }
    case 'threedrives':
      poly(p);
      [1, 3, 5].forEach((i, n) => letter(i, `Drive ${n + 1}`));
      legLabel(1, 2, [0, 1], [1, 2]);
      legLabel(2, 3, [1, 2], [2, 3]);
      legLabel(3, 4, [2, 3], [3, 4]);
      legLabel(4, 5, [3, 4], [4, 5]);
      break;
    case 'trianglepat':
      poly(p);
      push(k.line(a, c, extL, true, { width: 1 }));
      push(k.line(b, e, extL, true, { width: 1 }));
      ['A', 'B', 'C', 'D'].forEach((name, i) => letter(i, name));
      break;
    case 'elliottimpulse':
      poly(p);
      ['(0)', '(1)', '(2)', '(3)', '(4)', '(5)'].forEach((name, i) => letter(i, name));
      break;
    case 'elliottcorrective':
      poly(p);
      ['(0)', '(A)', '(B)', '(C)'].forEach((name, i) => letter(i, name));
      break;
    case 'cyclic':
    case 'timecycles': {
      const ia = proj.indexOf(d.points[0].t);
      const unit = proj.indexOf(d.points[1].t) - ia;
      if (unit === 0) break;
      const xs: (number | null)[] = [];
      for (let n = -CYCLES_EITHER_SIDE; n <= CYCLES_EITHER_SIDE; n++) xs.push(proj.x(proj.timeAt(ia + unit * n)));
      const seen = (x: number | null): x is number => x !== null && x >= -1 && x <= bounds.w + 1;
      if (d.tool === 'cyclic') {
        for (const x of xs) if (seen(x)) push(k.line([x, 0], [x, bounds.h], false, false, { width: 1 }));
        break;
      }
      const rise = Math.max(Math.abs(b[1] - a[1]), 20);
      for (let i = 0; i + 1 < xs.length; i++) {
        const x0 = xs[i];
        const x1 = xs[i + 1];
        if (x0 === null || x1 === null || (x0 < -1 && x1 < -1) || (x0 > bounds.w + 1 && x1 > bounds.w + 1)) continue;
        const arch: Pt[] = [];
        for (let s = 0; s <= 20; s++) arch.push([x0 + ((x1 - x0) * s) / 20, a[1] - rise * Math.sin((Math.PI * s) / 20)]);
        poly(arch, true, fill, { width: Math.min(width, 2) });
      }
      break;
    }
    case 'sine': {
      const span = b[0] - a[0];
      if (span === 0) break;
      const centre = (a[1] + b[1]) / 2;
      const swing = (a[1] - b[1]) / 2;
      const pts: Pt[] = [];
      for (let x = 0; x <= bounds.w; x += 3) pts.push([x, centre + swing * Math.cos((Math.PI * (x - a[0])) / span)]);
      poly(pts);
      break;
    }
    default:
      break;
  }
}
