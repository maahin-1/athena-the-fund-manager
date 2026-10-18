import type { Bounds } from './geometry';
import type { Projection } from './projection';

/** What the drawing controller needs from the chart. The Lightweight Charts renderer provides it; tests stand in. */
export interface DrawingHost {
  /** The chart's current mapping between bar times and prices and pixels, or null when there is no chart data. */
  projection(): Projection | null;
  /** The size of the price pane in CSS pixels, or null before the chart exists. */
  size(): Bounds | null;
  /** Set what paints drawings on top of the series (called inside the chart's own paint cycle); null stops painting. */
  onPaint(fn: ((ctx: CanvasRenderingContext2D, projection: Projection, size: Bounds) => void) | null): void;
  /** Ask for a repaint because only the drawings changed. */
  redraw(): void;
  /** Switch the chart's own pan and zoom off (while a drawing is dragged) and on again. */
  freeze(on: boolean): void;
}
