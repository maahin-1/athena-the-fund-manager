import type { Cell } from '../screenlogic';

/** The heatmap's colours: a figure on a diverging scale from red through a neutral to green, each figure with a fixed range so a colour means the same
 *  thing from one day to the next. A stock with no figure is grey, a colour no figure can take. */

export const GREY = 'rgb(58, 63, 75)';
type Rgb = [number, number, number];
const NEUTRAL: Rgb = [43, 48, 57];
const GREEN: Rgb = [38, 194, 129];
const RED: Rgb = [239, 83, 80];

export interface Scale {
  id: string;
  label: string;
  column: string;
  /** The figure that gets the full red and the one that gets the full green; beyond them the colour stays at the end. */
  lo: number;
  hi: number;
  /** The range in words, for the legend. */
  legend: string;
}

const returns = (id: string, label: string, edge: number): Scale => ({ id, label, column: id, lo: -edge, hi: edge, legend: `−${edge * 100}% to +${edge * 100}%` });

export const SCALES: Scale[] = [
  returns('ret_1d', '1 day', 0.03),
  returns('ret_1m', '1 month', 0.1),
  returns('ret_3m', '3 months', 0.2),
  returns('ret_6m', '6 months', 0.3),
  returns('ret_1y', '1 year', 0.5),
  { id: 'from_52w_high', label: 'From 52-week high', column: 'from_52w_high', lo: -0.4, hi: 0, legend: '−40% (red) to the high itself (green)' },
];

/** The scale of that id, or the first one for an id that is not known. */
export const scaleOf = (id: string): Scale => SCALES.find((s) => s.id === id) ?? SCALES[0];

const mix = (from: Rgb, to: Rgb, t: number): string => `rgb(${from.map((c, i) => Math.round(c + (to[i] - c) * t)).join(', ')})`;

export function colourFor(value: Cell, scale: Scale): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return GREY;
  const mid = (scale.lo + scale.hi) / 2;
  const t = Math.max(-1, Math.min(1, (value - mid) / ((scale.hi - scale.lo) / 2)));
  return t >= 0 ? mix(NEUTRAL, GREEN, t) : mix(NEUTRAL, RED, -t);
}
