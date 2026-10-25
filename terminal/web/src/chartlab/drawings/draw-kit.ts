/** What every drawing tool's geometry is made of: shapes in pixels, colours, and the line clipper. Shared by `geometry.ts` (the first tools) and `extra.ts` (the later ones). */
import type { Dash } from './model';

export interface Bounds { w: number; h: number }
export type Pt = [number, number];
export interface Paint { color: string; width: number; dash: Dash }

export type Shape =
  | ({ k: 'line'; x1: number; y1: number; x2: number; y2: number } & Paint)
  | ({ k: 'poly'; pts: Pt[]; closed: boolean; fill?: string } & Paint)
  | ({ k: 'rect'; x: number; y: number; w: number; h: number; fill?: string } & Paint)
  | ({ k: 'ellipse'; cx: number; cy: number; rx: number; ry: number; fill?: string } & Paint)
  | { k: 'text'; x: number; y: number; text: string; color: string; align: 'left' | 'center' | 'right'; bg?: string; size: number };


export const FIB_COLORS: Record<number, string> = {
  0: '#787b86', 0.236: '#f23645', 0.382: '#ff9800', 0.5: '#089981', 0.618: '#00bcd4', 0.786: '#2962ff', 1: '#787b86', 1.272: '#9c27b0', 1.618: '#f23645', 2.618: '#ff9800',
};
export const UP = '#089981';
export const DOWN = '#f23645';
export const FILL_ALPHA = 0.14;

/** `#rrggbb` with an alpha. */
export function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// ---- line clipping -------------------------------------------------------------------------------------------------------------------------

/** The part of the line through (x1,y1) and (x2,y2) that is in the pane: the segment only, or continued past either end, or both (Liang-Barsky). Null when nothing is in view. */
export function clipLine(x1: number, y1: number, x2: number, y2: number, b: Bounds, left: boolean, right: boolean): [number, number, number, number] | null {
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

