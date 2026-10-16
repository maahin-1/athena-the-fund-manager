import {
  CandlestickSeries, ColorType, createChart, CrosshairMode, HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type ISeriesApi, type SeriesType, type UTCTimestamp,
} from 'lightweight-charts';
import { applyTick, tickFitsChart, type Bar } from '../bars';
import type { Quote } from '../feed';
import { compact, pct, price, signed } from '../format';
import { bollinger, ema, INDICATOR_NAMES, macd, rsi, sma, type Series } from '../indicators';
import { barLimit, RANGES, type Range, type Timeframe } from '../ranges';
import { el, Panel, type AppContext } from './base';

const IST_OFFSET = 19_800;

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}

/** Candles with volume in the same pane, an OHLC legend, a last-price tag, the range row and the indicator picker. */
export class ChartPanel extends Panel {
  private chart: IChartApi | null = null;
  private candles: ISeriesApi<'Candlestick'> | null = null;
  private volume: ISeriesApi<'Histogram'> | null = null;
  private extras: ISeriesApi<SeriesType>[] = [];
  private bars: Bar[] = [];
  private range: Range = RANGES[0];
  private legend = el('div', 'legend');
  private note = el('div', 'note');
  private host = el('div', 'chart-host');
  private rangeRow = el('div', 'ranges');
  private picker = el('select', 'picker');
  private loadToken = 0;
  private repaint = 0;
  private watcher: MutationObserver | null = null;

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.start();
  }

  private tfOverride: Timeframe | null = null; // set when intraday bars are missing and daily bars are shown instead

  private get tf(): Timeframe {
    return this.tfOverride ?? this.range.tf;
  }

  private time(t: number): UTCTimestamp {
    return (this.tf === '1D' ? t : t + IST_OFFSET) as UTCTimestamp;
  }

  protected build(): void {
    this.range = RANGES.find((r) => r.label === this.state.range) ?? RANGES[0];
    const toolbar = el('div', 'chart-tools');
    for (const r of RANGES) {
      const b = el('button', 'rbtn', r.label);
      b.dataset.range = r.label;
      b.addEventListener('click', () => {
        this.range = r;
        this.state.range = r.label;
        this.ctx.changed();
        this.paintRanges();
        void this.load();
      });
      this.rangeRow.append(b);
    }
    this.picker.append(new Option('+ Indicator', ''));
    for (const n of INDICATOR_NAMES) this.picker.append(new Option(n, n));
    this.picker.addEventListener('change', () => {
      const v = this.picker.value;
      this.picker.value = '';
      if (!v) return;
      const list = this.state.indicators ?? [];
      this.state.indicators = list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
      this.ctx.changed();
      this.drawIndicators();
    });
    toolbar.append(this.rangeRow, this.picker);
    this.body.append(toolbar, this.legend, this.host, this.note);
    this.body.classList.add('chart-body');
    this.paintRanges();
    this.makeChart();
    this.watcher = new MutationObserver(() => this.applyTheme());
    this.watcher.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }

  private paintRanges(): void {
    this.rangeRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.range === this.range.label));
  }

  private makeChart(): void {
    const chart = createChart(this.host, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css('--bg-panel') }, textColor: css('--fg-dim'), fontFamily: 'inherit', attributionLogo: false, panes: { separatorColor: css('--border') } },
      grid: { vertLines: { color: css('--grid') }, horzLines: { color: css('--grid') } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: css('--border') },
      timeScale: { borderColor: css('--border'), timeVisible: true, secondsVisible: false, fixRightEdge: true, fixLeftEdge: true, rightOffset: 3 },
    });
    this.chart = chart;
    this.candles = chart.addSeries(CandlestickSeries, {
      upColor: css('--up'), downColor: css('--dn'), borderVisible: false, wickUpColor: css('--up'), wickDownColor: css('--dn'),
      priceLineStyle: LineStyle.Dotted, lastValueVisible: true,
    });
    // prices keep the top 70% of the pane and volume the bottom 18%, so the bars never sit on top of the candles
    this.candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.3 } });
    this.volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false });
    this.volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
    chart.subscribeCrosshairMove((p) => {
      const d = p.seriesData.get(this.candles as ISeriesApi<SeriesType>) as { open: number; high: number; low: number; close: number } | undefined;
      if (d && p.time !== undefined) {
        const bar = this.bars.find((b) => this.time(b.t) === p.time);
        this.showLegend({ o: d.open, h: d.high, l: d.low, c: d.close, v: bar?.v ?? 0, t: 0 });
      } else this.showLegend(this.bars[this.bars.length - 1]);
    });
  }

  private applyTheme(): void {
    this.chart?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: css('--bg-panel') }, textColor: css('--fg-dim'), panes: { separatorColor: css('--border') } },
      grid: { vertLines: { color: css('--grid') }, horzLines: { color: css('--grid') } },
      rightPriceScale: { borderColor: css('--border') },
      timeScale: { borderColor: css('--border') },
    });
    this.candles?.applyOptions({ upColor: css('--up'), downColor: css('--dn'), wickUpColor: css('--up'), wickDownColor: css('--dn') });
    this.paintVolume();
  }

  protected symbolChanged(symbol: string): void {
    this.titleEl.textContent = `${symbol} · ${this.range.label}`;
    this.ctx.feed.need(this.id, [symbol]);
    void this.load();
  }

  private async load(attempt = 0): Promise<void> {
    const symbol = this.symbol;
    if (!symbol || !this.chart || this.disposed) return;
    const token = ++this.loadToken;
    if (attempt === 0) this.tfOverride = null;
    this.titleEl.textContent = `${symbol} · ${this.range.label}`;
    const today = new Date();
    const limit = this.tf === '1D' ? (barLimit(this.range, today) ?? 5000) : 0;
    const url = `/api/bars?symbol=${encodeURIComponent(symbol)}&tf=${this.tf}${limit ? `&limit=${limit}` : ''}`;
    let body: { bars: Bar[]; note: string };
    try {
      body = await (await fetch(url)).json();
    } catch {
      this.note.textContent = 'could not reach the server';
      return;
    }
    if (token !== this.loadToken || this.disposed) return; // a newer request replaced this one, or the panel is gone
    let bars = body.bars;
    if (this.tf !== '1D' && typeof this.range.bars === 'number') bars = bars.slice(-this.range.bars);
    this.bars = bars;
    const notes = [this.range.note, body.note === 'warehouse busy' ? 'the warehouse is busy (a build is running); try again in a minute' : ''];
    this.note.textContent = notes.filter(Boolean).join(' ');
    if (body.note === 'loading' && attempt < 6) {
      this.note.textContent = 'loading intraday bars…';
      setTimeout(() => !this.disposed && void this.load(attempt + 1), 1500);
    } else if (!bars.length && this.tf !== '1D') {
      // no intraday bars (Yahoo is down, or the symbol has none): show the last six months of daily bars so the chart is never empty
      this.tfOverride = '1D';
      const daily = await fetch(`/api/bars?symbol=${encodeURIComponent(symbol)}&tf=1D&limit=126`).then((r) => r.json()).catch(() => null) as { bars: Bar[] } | null;
      if (token !== this.loadToken || this.disposed) return;
      this.bars = daily?.bars ?? [];
      this.note.textContent = this.bars.length ? 'No intraday bars are available right now; showing daily bars.' : `no bars for ${symbol}`;
    } else if (!bars.length && !this.note.textContent) this.note.textContent = `no ${this.tf} bars for ${symbol}`;
    this.paintBars();
  }

  private paintBars(): void {
    if (!this.candles || !this.volume) return;
    this.candles.setData(this.bars.map((b) => ({ time: this.time(b.t), open: b.o, high: b.h, low: b.l, close: b.c })));
    this.paintVolume();
    this.drawIndicators();
    this.chart?.timeScale().applyOptions({ timeVisible: this.tf !== '1D' });
    this.chart?.timeScale().fitContent();
    this.showLegend(this.bars[this.bars.length - 1]);
  }

  private paintVolume(): void {
    this.volume?.setData(this.bars.map((b) => ({ time: this.time(b.t), value: b.v, color: b.c >= b.o ? css('--up-dim') : css('--dn-dim') })));
  }

  private drawIndicators(): void {
    if (!this.chart) return;
    for (const s of this.extras) this.chart.removeSeries(s);
    this.extras = [];
    const closes = this.bars.map((b) => b.c);
    const line = (values: Series, color: string, pane = 0, width: 1 | 2 = 1) => {
      const s = this.chart!.addSeries(LineSeries, { color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, pane);
      s.setData(values.flatMap((v, i) => (v === null ? [] : [{ time: this.time(this.bars[i].t), value: v }])));
      this.extras.push(s);
      return s;
    };
    for (const name of this.state.indicators ?? []) {
      if (name === 'EMA 20') line(ema(closes, 20), css('--ind1'));
      else if (name === 'SMA 50') line(sma(closes, 50), css('--ind2'));
      else if (name === 'Bollinger 20') {
        const b = bollinger(closes, 20, 2);
        line(b.upper, css('--ind3'));
        line(b.mid, css('--ind3'));
        line(b.lower, css('--ind3'));
      } else if (name === 'RSI 14') {
        const s = line(rsi(closes, 14), css('--ind1'), 1, 2);
        s.createPriceLine({ price: 70, color: css('--dn'), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' });
        s.createPriceLine({ price: 30, color: css('--up'), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: '' });
      } else if (name === 'MACD') {
        const m = macd(closes, 12, 26, 9);
        line(m.macd, css('--ind1'), 2);
        line(m.signal, css('--ind2'), 2);
        const h = this.chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, 2);
        h.setData(m.hist.flatMap((v, i) => (v === null ? [] : [{ time: this.time(this.bars[i].t), value: v, color: v >= 0 ? css('--up-dim') : css('--dn-dim') }])));
        this.extras.push(h);
      }
    }
  }

  private showLegend(b: Bar | undefined): void {
    if (!b) {
      this.legend.textContent = '';
      return;
    }
    const prev = this.bars.length > 1 ? this.bars[this.bars.length - 2].c : b.o;
    const change = b.c - prev;
    const dir = change >= 0 ? 'up' : 'dn';
    this.legend.replaceChildren();
    const cell = (k: string, v: string, cls = '') => {
      this.legend.append(el('span', 'lk', k), el('span', `lv ${cls}`, v));
    };
    cell('O', price(b.o), dir);
    cell('H', price(b.h), dir);
    cell('L', price(b.l), dir);
    cell('C', price(b.c), dir);
    cell('', `${signed(change)} (${pct(prev ? (change / prev) * 100 : 0)})`, dir);
    if (b.v) cell('Vol', compact(b.v));
  }

  protected onQuotes(quotes: Quote[]): void {
    const q = quotes.find((x) => x.s === this.symbol);
    if (this.disposed || !q || !this.candles || this.bars.length === 0) return;
    const ts = q.t ? q.t / 1000 : Date.now() / 1000;
    const last = this.bars[this.bars.length - 1];
    if (!tickFitsChart(last, q.l, ts, this.tf)) return;
    const next = applyTick(last, q.l, ts, this.tf);
    if (next === last) return;
    if (next.t === last.t) this.bars[this.bars.length - 1] = next;
    else this.bars.push(next);
    this.candles.update({ time: this.time(next.t), open: next.o, high: next.h, low: next.l, close: next.c });
    this.showLegend(next);
    if (!this.repaint) {
      this.repaint = requestAnimationFrame(() => {
        this.repaint = 0;
        if (!this.disposed && (this.state.indicators ?? []).length) this.drawIndicators();
      });
    }
  }

  dispose(): void {
    this.watcher?.disconnect();
    if (this.repaint) cancelAnimationFrame(this.repaint);
    this.chart?.remove();
    super.dispose();
  }
}
