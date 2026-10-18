import { customSeriesDefaultOptions } from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type { CustomData, CustomSeriesOptions, CustomSeriesWhitespaceData, ICustomSeriesPaneRenderer, ICustomSeriesPaneView, PaneRendererCustomData, Time } from 'lightweight-charts';
import { highLowShapes, kagiShapes, pnfShapes, volumeShapes, type Datum, type Item, type Role, type Shape } from './shapes';

export type ShapeMode = 'highlow' | 'volcandles' | 'kagi' | 'pnf';

/** One item of the series: the bar, line or column with the prices and the extras its type needs. */
export interface ShapeData extends CustomData<Time>, Datum {}

export interface ShapeOptions extends CustomSeriesOptions {
  mode: ShapeMode;
  up: string;
  down: string;
  accent: string;
}

/** The custom series behind high-low, volume candles, Kagi and point and figure. It owns no geometry: `update` keeps the visible items and `draw` hands them to
 *  the pure functions in `shapes.ts` and paints what they return. */
export class ShapeView implements ICustomSeriesPaneView<Time, ShapeData, ShapeOptions> {
  private data: PaneRendererCustomData<Time, ShapeData> | null = null;
  private options: ShapeOptions | null = null;

  private readonly painter: ICustomSeriesPaneRenderer = {
    draw: (target, price) => {
      const data = this.data;
      const options = this.options;
      if (!data || !options || !data.visibleRange) return;
      const { from, to } = data.visibleRange;
      const items: Item[] = data.bars.filter((b) => b.time >= from - 1 && b.time <= to + 1).map((b) => ({ x: b.x, d: b.originalData }));
      const y = (p: number): number | null => price(p);
      const spacing = data.barSpacing;
      const shapes = shapesFor(options.mode, items, y, spacing);
      paint(target, shapes, options);
    },
  };

  renderer(): ICustomSeriesPaneRenderer {
    return this.painter;
  }

  update(data: PaneRendererCustomData<Time, ShapeData>, options: ShapeOptions): void {
    this.data = data;
    this.options = options;
  }

  priceValueBuilder(d: ShapeData): number[] {
    return [d.high, d.low, d.close];
  }

  isWhitespace(d: ShapeData | CustomSeriesWhitespaceData<Time>): d is CustomSeriesWhitespaceData<Time> {
    return (d as ShapeData).close === undefined;
  }

  defaultOptions(): ShapeOptions {
    return { ...customSeriesDefaultOptions, mode: 'highlow', up: '#26a69a', down: '#ef5350', accent: '#4c8dff' };
  }
}

/** The shapes of the items for a type. Volume candles scale their bodies to the largest volume in view. */
export function shapesFor(mode: ShapeMode, items: Item[], y: (p: number) => number | null, spacing: number): Shape[] {
  switch (mode) {
    case 'highlow':
      return highLowShapes(items, y, spacing);
    case 'volcandles':
      return volumeShapes(items, y, spacing, Math.max(0, ...items.map((i) => i.d.volume)));
    case 'kagi':
      return kagiShapes(items, y, spacing);
    case 'pnf':
      return pnfShapes(items, y, spacing);
  }
}

function paint(target: CanvasRenderingTarget2D, shapes: Shape[], options: ShapeOptions): void {
  const colour: Record<Role, string> = { up: options.up, down: options.down, accent: options.accent, thick: options.up, thin: options.down };
  target.useMediaCoordinateSpace(({ context: ctx }) => {
    for (const s of shapes) {
      if (s.k === 'rect') {
        ctx.fillStyle = colour[s.role];
        ctx.fillRect(s.x, s.y, s.w, s.h);
      } else if (s.k === 'line') {
        ctx.strokeStyle = colour[s.role];
        ctx.lineWidth = s.width;
        ctx.beginPath();
        ctx.moveTo(s.x1, s.y1);
        ctx.lineTo(s.x2, s.y2);
        ctx.stroke();
      } else {
        ctx.strokeStyle = colour[s.role];
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.ellipse(s.cx, s.cy, s.rx, s.ry, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  });
}
