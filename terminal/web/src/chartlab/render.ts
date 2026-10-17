import {
  AreaSeries, BarSeries, CandlestickSeries, ColorType, createChart, CrosshairMode, HistogramSeries, LineSeries, LineStyle, PriceScaleMode,
  type IChartApi, type ISeriesApi, type SeriesType, type UTCTimestamp,
} from 'lightweight-charts';
import type { Bar } from '../bars';
import type { ChartModel } from './model';
import type { Series } from './maths';

const IST_OFFSET = 19_800;

/** What the view needs from a drawing engine; the tests give it a stand-in so no canvas is needed. */
export interface ChartRenderer {
  /** Rebuild everything from the model (bars, type, scale, compare, indicators). */
  paint(model: ChartModel): void;
  /** Only the last bar moved: update it and the indicators without rebuilding. */
  tick(model: ChartModel): void;
  fit(): void;
  /** The bar index under the crosshair, or null when it leaves the chart. */
  onCrosshair(cb: (index: number | null) => void): void;
  /** The element of an indicator pane (0 is the price pane), for placing legends. */
  paneElement(index: number): HTMLElement | null;
  /** The pane an indicator instance is drawn in. */
  paneOf(uid: string): number;
  snapshot(): HTMLCanvasElement | null;
  /** Re-read the page's theme colours for the chart frame (the view then repaints the series). */
  applyTheme(): void;
  dispose(): void;
}

export type RendererFactory = (host: HTMLElement) => ChartRenderer;

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}

const MODES = { auto: PriceScaleMode.Normal, log: PriceScaleMode.Logarithmic, percent: PriceScaleMode.Percentage };

export const lightweightRenderer: RendererFactory = (host) => {
  const chart: IChartApi = createChart(host, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: css('--bg-panel') }, textColor: css('--fg-dim'), fontFamily: 'inherit', attributionLogo: false, panes: { separatorColor: css('--border') } },
    grid: { vertLines: { color: css('--grid') }, horzLines: { color: css('--grid') } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: css('--border') },
    timeScale: { borderColor: css('--border'), timeVisible: false, secondsVisible: false, fixRightEdge: true, rightOffset: 3 },
  });
  let series: ISeriesApi<SeriesType>[] = [];
  let main: ISeriesApi<SeriesType> | null = null;
  let volume: ISeriesApi<'Histogram'> | null = null;
  let compare: ISeriesApi<'Line'> | null = null;
  let intraday = false;
  let times: number[] = [];
  let crosshair: (i: number | null) => void = () => {};
  const panes = new Map<string, number>();
  const parts = new Map<string, { key: string; kind: 'line' | 'histogram'; s: ISeriesApi<SeriesType> }[]>();

  const time = (t: number): UTCTimestamp => (intraday ? t + IST_OFFSET : t) as UTCTimestamp;
  const points = (bars: Bar[], values: Series) => values.flatMap((v, i) => (v === null ? [] : [{ time: time(bars[i].t), value: v }]));

  chart.subscribeCrosshairMove((p) => {
    if (p.time === undefined) return crosshair(null);
    const i = times.indexOf(p.time as number);
    crosshair(i >= 0 ? i : null);
  });

  const mainData = (model: ChartModel): unknown[] => {
    const bars = model.displayBars();
    return model.type === 'line' || model.type === 'area'
      ? bars.map((b) => ({ time: time(b.t), value: b.c }))
      : bars.map((b) => ({ time: time(b.t), open: b.o, high: b.h, low: b.l, close: b.c }));
  };

  const volumeData = (bars: Bar[]) => bars.map((b) => ({ time: time(b.t), value: b.v, color: b.c >= b.o ? css('--up-dim') : css('--dn-dim') }));

  return {
    paint(model) {
      for (const s of series) chart.removeSeries(s);
      series = [];
      panes.clear();
      parts.clear();
      intraday = model.tf !== '1D';
      times = model.bars.map((b) => time(b.t) as number);
      chart.priceScale('right').applyOptions({ mode: MODES[model.scale] });
      chart.timeScale().applyOptions({ timeVisible: intraday });
      const up = css('--up');
      const dn = css('--dn');
      if (model.type === 'line') main = chart.addSeries(LineSeries, { color: css('--accent'), lineWidth: 2 });
      else if (model.type === 'area') main = chart.addSeries(AreaSeries, { lineColor: css('--accent'), topColor: css('--up-dim'), bottomColor: 'rgba(0,0,0,0)', lineWidth: 2 });
      else if (model.type === 'bars') main = chart.addSeries(BarSeries, { upColor: up, downColor: dn });
      else if (model.type === 'hollow') main = chart.addSeries(CandlestickSeries, { upColor: 'rgba(0,0,0,0)', downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn, borderVisible: true });
      else main = chart.addSeries(CandlestickSeries, { upColor: up, downColor: dn, borderVisible: false, wickUpColor: up, wickDownColor: dn });
      main.setData(mainData(model) as never);
      main.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.3 } });
      series.push(main);
      volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
      volume.setData(volumeData(model.bars));
      series.push(volume);
      compare = null;
      if (model.compare) {
        compare = chart.addSeries(LineSeries, { color: css('--ind3'), lineWidth: 2, title: model.compare.symbol, priceLineVisible: false });
        compare.setData(points(model.bars, model.compareSeries()));
        series.push(compare);
      }
      let next = 1;
      for (const inst of model.indicators.filter((i) => !i.hidden)) {
        const def = model.def(inst);
        const pane = def.pane === 'overlay' ? 0 : next++;
        panes.set(inst.uid, pane);
        const values = model.series(inst.uid);
        const list: { key: string; kind: 'line' | 'histogram'; s: ISeriesApi<SeriesType> }[] = [];
        for (const out of def.outputs) {
          const color = inst.colors[out.key] ?? css(out.color);
          const data = values[out.key] ?? [];
          let s: ISeriesApi<SeriesType>;
          if (out.kind === 'histogram') {
            s = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane);
            s.setData(data.flatMap((v, i) => (v === null ? [] : [{ time: time(model.bars[i].t), value: v, color: inst.colors[out.key] ?? (v >= 0 ? css('--up-dim') : css('--dn-dim')) }])) as never);
          } else {
            const dots = def.id === 'sar';
            s = chart.addSeries(LineSeries, { color, lineWidth: out.width ?? 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...(dots ? { lineVisible: false, pointMarkersVisible: true } : {}) }, pane);
            s.setData(points(model.bars, data) as never);
          }
          series.push(s);
          list.push({ key: out.key, kind: out.kind, s });
        }
        parts.set(inst.uid, list);
        const first = list.find((p) => p.kind === 'line')?.s;
        if (first) for (const level of def.levels ?? []) first.createPriceLine({ price: level, color: css('--fg-dim'), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' });
      }
    },

    tick(model) {
      if (!main) return;
      const data = mainData(model);
      if (model.type === 'heikin') main.setData(data as never);
      else main.update(data[data.length - 1] as never);
      const last = model.bars[model.bars.length - 1];
      times = model.bars.map((b) => time(b.t) as number);
      volume?.update(volumeData([last])[0]);
      if (compare && model.compare) compare.setData(points(model.bars, model.compareSeries()));
      for (const [uid, list] of parts) {
        const values = model.series(uid);
        for (const p of list) {
          const data2 = values[p.key] ?? [];
          if (p.kind === 'line') (p.s as ISeriesApi<'Line'>).setData(points(model.bars, data2) as never);
          else (p.s as ISeriesApi<'Histogram'>).setData(data2.flatMap((v, i) => (v === null ? [] : [{ time: time(model.bars[i].t), value: v, color: v >= 0 ? css('--up-dim') : css('--dn-dim') }])) as never);
        }
      }
    },

    fit: () => chart.timeScale().fitContent(),
    onCrosshair: (cb) => (crosshair = cb),
    paneElement: (index) => chart.panes()[index]?.getHTMLElement() ?? null,
    paneOf: (uid) => panes.get(uid) ?? 0,
    snapshot: () => {
      try {
        return chart.takeScreenshot();
      } catch {
        return null;
      }
    },
    applyTheme: () =>
      chart.applyOptions({
        layout: { background: { type: ColorType.Solid, color: css('--bg-panel') }, textColor: css('--fg-dim'), panes: { separatorColor: css('--border') } },
        grid: { vertLines: { color: css('--grid') }, horzLines: { color: css('--grid') } },
        rightPriceScale: { borderColor: css('--border') },
        timeScale: { borderColor: css('--border') },
      }),
    dispose: () => chart.remove(),
  };
};
