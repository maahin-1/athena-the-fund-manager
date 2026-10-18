import type { IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesPrimitive, PrimitivePaneViewZOrder, SeriesAttachedParameter, SeriesType, Time } from 'lightweight-charts';
import type { Bounds } from './geometry';

export type Painter = (ctx: CanvasRenderingContext2D, size: Bounds) => void;

/** A Lightweight Charts series primitive that runs one painter inside the chart's own paint cycle, on top of the series, in pane (CSS pixel) coordinates. The chart calls it whenever
 *  it repaints (scroll, zoom, resize, a new bar), and `redraw` asks for one when only the drawings changed. */
export class DrawingPrimitive implements ISeriesPrimitive<Time> {
  private params: SeriesAttachedParameter<Time, SeriesType> | null = null;
  private painter: Painter | null = null;

  private readonly renderer: IPrimitivePaneRenderer = {
    draw: (target) => {
      const paint = this.painter;
      if (!paint) return;
      target.useMediaCoordinateSpace(({ context, mediaSize }) => paint(context, { w: mediaSize.width, h: mediaSize.height }));
    },
  };

  private readonly view: IPrimitivePaneView = {
    zOrder: (): PrimitivePaneViewZOrder => 'top',
    renderer: () => this.renderer,
  };

  private readonly views = [this.view];

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  attached(params: SeriesAttachedParameter<Time, SeriesType>): void {
    this.params = params;
  }

  detached(): void {
    this.params = null;
  }

  setPainter(painter: Painter | null): void {
    this.painter = painter;
    this.redraw();
  }

  redraw(): void {
    this.params?.requestUpdate();
  }
}
