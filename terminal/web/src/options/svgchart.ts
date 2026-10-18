/** A small line chart as SVG for the volatility smile, the term structure and the payoff: some lines, vertical marks and a labelled frame. No library, no canvas. */

export interface Line {
  points: { x: number; y: number }[];
  cls: string;
}

export interface Mark {
  x: number;
  label: string;
  cls: string;
}

export interface ChartOptions {
  width?: number;
  height?: number;
  lines: Line[];
  marks?: Mark[];
  /** Draw a horizontal line at zero (the payoff's). */
  zero?: boolean;
  x: (v: number) => string;
  y: (v: number) => string;
  /** The label read by assistive technology and used as the element's title. */
  label: string;
}

const NS = 'http://www.w3.org/2000/svg';
const PAD = { left: 52, right: 10, top: 10, bottom: 22 };

function node<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}, text = ''): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  if (text) e.textContent = text;
  return e;
}

/** `count` round tick values between `lo` and `hi`, inclusive of the ends' neighbourhood. */
export function ticks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v / step) * step);
  return out;
}

/** The chart's extent: the data's, with a margin, and flat data given some height. */
export function extent(values: number[]): [number, number] {
  if (!values.length) return [0, 1];
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    const d = Math.abs(lo) * 0.05 || 1;
    return [lo - d, hi + d];
  }
  const m = (hi - lo) * 0.06;
  lo -= m;
  hi += m;
  return [lo, hi];
}

export function lineChart(o: ChartOptions): SVGSVGElement {
  const width = o.width ?? 360;
  const height = o.height ?? 190;
  const svg = node('svg', { viewBox: `0 0 ${width} ${height}`, width: '100%', height, role: 'img', 'aria-label': o.label, class: 'opt-svg' });
  svg.append(node('title', {}, o.label));
  const xs = o.lines.flatMap((l) => l.points.map((p) => p.x));
  const ys = o.lines.flatMap((l) => l.points.map((p) => p.y));
  if (!xs.length) {
    svg.append(node('text', { x: width / 2, y: height / 2, 'text-anchor': 'middle', class: 'opt-empty' }, 'nothing to draw yet'));
    return svg;
  }
  const [x0, x1] = [Math.min(...xs, ...(o.marks ?? []).map((m) => m.x)), Math.max(...xs, ...(o.marks ?? []).map((m) => m.x))];
  const [y0, y1] = extent([...ys, ...(o.zero ? [0] : [])]);
  const sx = (v: number): number => PAD.left + ((v - x0) / (x1 - x0 || 1)) * (width - PAD.left - PAD.right);
  const sy = (v: number): number => PAD.top + (1 - (v - y0) / (y1 - y0 || 1)) * (height - PAD.top - PAD.bottom);
  for (const t of ticks(y0, y1)) {
    svg.append(node('line', { x1: PAD.left, x2: width - PAD.right, y1: sy(t), y2: sy(t), class: 'opt-grid' }));
    svg.append(node('text', { x: PAD.left - 4, y: sy(t) + 3, 'text-anchor': 'end', class: 'opt-tick' }, o.y(t)));
  }
  for (const t of ticks(x0, x1, 4)) svg.append(node('text', { x: sx(t), y: height - 6, 'text-anchor': 'middle', class: 'opt-tick' }, o.x(t)));
  if (o.zero && y0 < 0 && y1 > 0) svg.append(node('line', { x1: PAD.left, x2: width - PAD.right, y1: sy(0), y2: sy(0), class: 'opt-zero' }));
  for (const m of o.marks ?? []) {
    svg.append(node('line', { x1: sx(m.x), x2: sx(m.x), y1: PAD.top, y2: height - PAD.bottom, class: `opt-mark ${m.cls}` }));
    svg.append(node('text', { x: sx(m.x) + 3, y: PAD.top + 9, class: `opt-marklabel ${m.cls}` }, m.label));
  }
  for (const line of o.lines) {
    if (line.points.length === 1) svg.append(node('circle', { cx: sx(line.points[0].x), cy: sy(line.points[0].y), r: 3, class: `opt-line ${line.cls}` }));
    else svg.append(node('polyline', { points: line.points.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' '), fill: 'none', class: `opt-line ${line.cls}` }));
  }
  return svg;
}
