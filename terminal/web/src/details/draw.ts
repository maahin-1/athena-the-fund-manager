/** SVG drawings for the Details panel: the technicals dial and the seasonal paths. */
import type { Seasonals } from './seasonals';

const NS = 'http://www.w3.org/2000/svg';

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

/** Point on the dial: angle 0 is the far left, 180 the far right, going over the top. */
const at = (cx: number, cy: number, r: number, deg: number): [number, number] => [cx + r * Math.cos(Math.PI - (deg * Math.PI) / 180), cy - r * Math.sin(Math.PI - (deg * Math.PI) / 180)];

/** A half dial in five bands (strong sell to strong buy) with a needle at `score` (-1..1). */
export function gaugeSvg(score: number): SVGSVGElement {
  const root = svg('svg', { viewBox: '0 0 200 112', class: 'dial', role: 'img', 'aria-label': 'technicals dial' });
  const bands = ['var(--dn)', 'var(--dn-dim)', 'var(--fg-dim)', 'var(--up-dim)', 'var(--up)'];
  bands.forEach((fill, i) => {
    const [x1, y1] = at(100, 100, 80, i * 36 + 1);
    const [x2, y2] = at(100, 100, 80, (i + 1) * 36 - 1);
    const [x3, y3] = at(100, 100, 56, (i + 1) * 36 - 1);
    const [x4, y4] = at(100, 100, 56, i * 36 + 1);
    root.append(svg('path', { d: `M${x1} ${y1} A80 80 0 0 1 ${x2} ${y2} L${x3} ${y3} A56 56 0 0 0 ${x4} ${y4} Z`, fill, class: 'band' }));
  });
  const clamped = Math.max(-1, Math.min(1, Number.isFinite(score) ? score : 0));
  const [nx, ny] = at(100, 100, 70, ((clamped + 1) / 2) * 180);
  root.append(svg('line', { x1: 100, y1: 100, x2: nx, y2: ny, class: 'needle' }), svg('circle', { cx: 100, cy: 100, r: 5, class: 'hub' }));
  const sell = svg('text', { x: 20, y: 110, class: 'dial-end' });
  sell.textContent = 'Sell';
  const buy = svg('text', { x: 180, y: 110, class: 'dial-end', 'text-anchor': 'end' });
  buy.textContent = 'Buy';
  root.append(sell, buy);
  return root;
}

const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

/** One line per year over the calendar year, the current year bold and the average dashed, with a zero line and month marks. */
export function seasonalsSvg(s: Seasonals): SVGSVGElement {
  const W = 300;
  const H = 130;
  const pad = { l: 30, r: 6, t: 8, b: 16 };
  const values = [...s.years.flatMap((y) => y.points.map((p) => p[1])), ...s.average.map((p) => p[1]), 0];
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < 1) {
    lo -= 0.5;
    hi += 0.5;
  }
  const x = (f: number) => pad.l + f * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'seas', role: 'img', 'aria-label': 'seasonal paths' });
  root.append(svg('line', { x1: pad.l, x2: W - pad.r, y1: y(0), y2: y(0), class: 'zero' }));
  const label = (txt: string, px: number, py: number, anchor = 'middle') => {
    const t = svg('text', { x: px, y: py, class: 'axis', 'text-anchor': anchor });
    t.textContent = txt;
    root.append(t);
  };
  label(`${hi.toFixed(0)}%`, pad.l - 3, pad.t + 6, 'end');
  label(`${lo.toFixed(0)}%`, pad.l - 3, H - pad.b, 'end');
  MONTHS.forEach((m, i) => label(m, x((i + 0.5) / 12), H - 4));
  const path = (pts: [number, number][]) => pts.map(([f, v], i) => `${i ? 'L' : 'M'}${x(f).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  s.years.forEach((yr, i) => {
    const current = i === s.years.length - 1;
    const line = svg('path', { d: path(yr.points), class: current ? 'yr cur' : 'yr', 'data-year': yr.year });
    const title = svg('title');
    title.textContent = String(yr.year);
    line.append(title);
    root.append(line);
  });
  root.append(svg('path', { d: path(s.average), class: 'avg' }));
  return root;
}
