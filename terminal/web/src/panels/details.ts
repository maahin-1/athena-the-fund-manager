import type { Bar } from '../bars';
import { gaugeSvg, seasonalsSvg } from '../details/draw';
import { performance, type PerfRow } from '../details/perf';
import { seasonals } from '../details/seasonals';
import { frameBars, technicals, type Frame, type Label, type Summary, type Vote } from '../details/technicals';
import type { Quote } from '../feed';
import { compact, pct, price, signed } from '../format';
import { isIndexSymbol } from '../watchlists/model';
import { el, Panel, type AppContext } from './base';

interface Known {
  name: string;
  kind: string;
  isin: string | null;
}

const FRAMES: Frame[] = ['1D', '1W', '1M'];
const SECTIONS: [string, string][] = [['facts', 'Key facts'], ['perf', 'Performance'], ['tech', 'Technicals'], ['seas', 'Seasonals']];
const ENOUGH = 30; // bars needed for the sections that are drawn from prices

const dirOf = (n: number | null): string => (n === null || n === 0 ? 'flat' : n > 0 ? 'up' : 'dn');
const dash = '—';
const ratio = (n: unknown): string => (typeof n === 'number' ? n.toFixed(2) : dash);
const fraction = (n: unknown): string => (typeof n === 'number' ? `${(n * 100).toFixed(1)}%` : dash);
const verdictClass = (label: Label): string => (label.endsWith('buy') || label === 'Buy' ? 'up' : label.endsWith('sell') || label === 'Sell' ? 'dn' : 'flat');

/** Everything about the linked symbol in one place: key facts, performance over standard periods, a technicals dial, and seasonal paths. Stocks and ETFs use the stored market screen and bars; indices use the exchange's index snapshot and stored index bars. */
export class DetailsPanel extends Panel {
  private head = el('div', 'det-head');
  private sections = new Map<string, { wrap: HTMLElement; body: HTMLElement }>();
  private bars: Bar[] | null = null;
  private loading = false;
  private info: Known | null = null;
  private frame: Frame = '1D';
  private token = 0;
  private lastPaint = 0;
  private offs: (() => void)[] = [];

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.setTitle('Details');
    this.start();
  }

  protected build(): void {
    const root = el('div', 'det');
    root.append(this.head);
    for (const [key, title] of SECTIONS) {
      const wrap = el('section', `det-sec det-${key}`);
      const toggle = el('button', 'det-toggle', title);
      toggle.setAttribute('aria-expanded', String(!this.collapsed(key)));
      const body = el('div', 'det-body');
      body.hidden = this.collapsed(key);
      toggle.addEventListener('click', () => {
        const shut = !body.hidden;
        body.hidden = shut;
        toggle.setAttribute('aria-expanded', String(!shut));
        const set = new Set(this.state.collapsed ?? []);
        if (shut) set.add(key);
        else set.delete(key);
        this.state.collapsed = [...set];
        this.ctx.changed();
      });
      wrap.append(toggle, body);
      root.append(wrap);
      this.sections.set(key, { wrap, body });
    }
    this.body.append(root);
    this.offs.push(this.ctx.market.subscribe(() => this.paintFacts()), this.ctx.indices.subscribe(() => this.paintLive()));
    this.paintAll();
  }

  private collapsed(key: string): boolean {
    return (this.state.collapsed ?? []).includes(key);
  }

  private section(key: string): HTMLElement {
    return this.sections.get(key)!.body;
  }

  // ---- data ----------------------------------------------------------------------------------------------------------------------------

  protected symbolChanged(symbol: string): void {
    this.setTitle(`Details · ${symbol}`);
    const mine = ++this.token;
    this.bars = null;
    this.info = null;
    this.loading = true;
    if (!isIndexSymbol(symbol)) this.ctx.feed.need(this.id, [symbol]);
    this.paintAll();
    void this.ctx.history.get(symbol).then((bars) => {
      if (mine !== this.token || this.disposed) return;
      this.bars = bars;
      this.loading = false;
      this.paintAll();
    });
    void fetch(`/api/known?symbol=${encodeURIComponent(symbol)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((k: { known?: boolean; name?: string; kind?: string; isin?: string | null } | null) => {
        if (mine !== this.token || this.disposed || !k?.known) return;
        this.info = { name: k.name ?? symbol, kind: k.kind ?? '', isin: k.isin ?? null };
        this.paintHead();
        this.paintFacts();
      })
      .catch(() => {});
  }

  protected onQuotes(quotes: Quote[]): void {
    if (!quotes.some((q) => q.s === this.symbol)) return;
    const now = Date.now();
    if (now - this.lastPaint < 1000) return;
    this.lastPaint = now;
    this.paintLive();
  }

  private quote(): Quote | undefined {
    return this.ctx.feed.quotes.get(this.symbol);
  }

  /** The latest price: the live quote or index value when there is one, else the last stored close. */
  private livePrice(): number | undefined {
    if (isIndexSymbol(this.symbol)) return this.ctx.indices.get(this.symbol)?.l;
    const q = this.quote();
    return q && q.l > 0 ? q.l : undefined;
  }

  // ---- drawing -------------------------------------------------------------------------------------------------------------------------

  private paintAll(): void {
    this.paintHead();
    this.paintFacts();
    this.paintPerf();
    this.paintTech();
    this.paintSeas();
  }

  private paintLive(): void {
    this.paintHead();
    this.paintFacts();
    this.paintPerf();
  }

  private note(text: string): HTMLElement {
    return el('div', 'note', text);
  }

  private paintHead(): void {
    this.head.replaceChildren();
    if (!this.symbol) return void this.head.append(this.note('pick a symbol'));
    const bars = this.bars;
    const live = this.livePrice();
    const lastBar = bars && bars.length ? bars[bars.length - 1] : null;
    const last = live ?? lastBar?.c ?? null;
    let prev: number | null = null;
    if (isIndexSymbol(this.symbol)) prev = this.ctx.indices.get(this.symbol)?.pc ?? null;
    else prev = this.quote()?.pc ?? null;
    if (prev === null && bars && bars.length > 1) prev = bars[bars.length - (live === undefined ? 2 : 1)].c;
    const change = last !== null && prev ? last - prev : null;
    const name = this.info?.name ?? '';
    const top = el('div', 'det-name');
    top.append(el('b', '', this.symbol), el('span', 'det-full', name));
    const px = el('div', `det-price ${dirOf(change)}`);
    px.append(el('span', 'big', last === null ? dash : price(last)), el('span', 'sub', change === null || !prev ? '' : `${signed(change)}  ${pct((change / prev) * 100)}`));
    this.head.append(top, px);
  }

  private paintFacts(): void {
    const box = this.section('facts');
    box.replaceChildren();
    if (!this.symbol) return;
    const grid = el('div', 'facts');
    const fact = (k: string, v: string, title = '') => {
      const value = el('span', 'v', v);
      if (title) value.title = title;
      grid.append(el('span', 'k', k), value);
    };
    const opt = (n: number | null | undefined) => (typeof n === 'number' ? price(n) : dash);
    let range: [number | null, number | null, number | null] | null = null; // low, high, last
    if (isIndexSymbol(this.symbol)) {
      const ix = this.ctx.indices.get(this.symbol);
      if (!ix) {
        box.append(this.note('no live index figures yet'));
        return;
      }
      fact('Type', 'Index');
      fact('Day range', ix.lo !== null && ix.h !== null ? `${price(ix.lo)} – ${price(ix.h)}` : dash);
      fact('P/E', ratio(ix.pe));
      fact('P/B', ratio(ix.pb));
      fact('Dividend yield', ix.dy === null ? dash : `${ix.dy.toFixed(2)}%`);
      fact('Advances / declines', ix.adv !== null && ix.dec !== null ? `${ix.adv} / ${ix.dec}` : dash);
      fact('A week / month / year ago', [ix.w1, ix.m1, ix.y1].map(opt).join(' · '));
      range = [ix.l52, ix.h52, ix.l];
      fact('52 week', ix.l52 !== null && ix.h52 !== null ? `${price(ix.l52)} – ${price(ix.h52)}` : dash);
    } else {
      const q = this.quote();
      const row = this.ctx.market.get(this.symbol);
      fact('Type', this.info?.kind ? this.info.kind.toUpperCase() : dash);
      fact('Sector', typeof row?.sector === 'string' && row.sector ? row.sector : dash);
      fact('ISIN', this.info?.isin ?? dash);
      const days = this.bars ? this.bars.slice(-20) : [];
      fact('Day range', q && q.h !== null && q.lo !== null ? `${price(q.lo)} – ${price(q.h)}` : dash);
      fact('Volume', q?.v == null ? dash : compact(q.v));
      fact('Avg volume (20 d)', days.length === 20 ? compact(days.reduce((a, b) => a + b.v, 0) / 20) : dash);
      fact('Circuit', q && q.lb !== null && q.ub !== null ? `${price(q.lb)} – ${price(q.ub)}` : dash);
      range = [q?.l52 ?? null, q?.h52 ?? null, q?.l ?? this.bars?.[this.bars.length - 1]?.c ?? null];
      fact('52 week', q && q.l52 !== null && q.h52 !== null ? `${price(q.l52)} – ${price(q.h52)}` : dash);
      const mcap = row?.market_cap_cr;
      fact('Market cap', typeof mcap === 'number' ? `Rs ${Math.round(mcap).toLocaleString('en-IN')} cr` : dash, 'from the stored statements and prices');
      fact('P/E', ratio(row?.pe), 'price over the last year\'s earnings, from the stored statements');
      fact('P/B', ratio(row?.pb));
      fact('ROE', fraction(row?.roe));
      fact('Net margin', fraction(row?.net_margin));
      fact('Debt / equity', ratio(row?.debt_to_equity));
      fact('Dividend yield', fraction(row?.dividend_yield));
      fact('Revenue growth', fraction(row?.revenue_growth));
    }
    box.append(grid);
    if (range && range[0] !== null && range[1] !== null && range[2] !== null && range[1] > range[0]) {
      const at = Math.max(0, Math.min(1, (range[2] - range[0]) / (range[1] - range[0])));
      const bar = el('div', 'rng');
      bar.title = `${(at * 100).toFixed(0)}% of the way from the 52-week low to the high`;
      const mark = el('i');
      mark.style.left = `${(at * 100).toFixed(1)}%`;
      bar.append(mark);
      box.append(bar);
    }
    if (!isIndexSymbol(this.symbol) && this.ctx.market.loaded && !this.ctx.market.get(this.symbol)) box.append(this.note('not in the stored market screen'));
  }

  private needBars(box: HTMLElement): boolean {
    if (this.loading) {
      box.append(this.note('loading prices…'));
      return false;
    }
    if (!this.bars) {
      box.append(this.note('prices are not available right now'));
      return false;
    }
    if (this.bars.length < ENOUGH) {
      box.append(this.note('not enough history for this'));
      return false;
    }
    return true;
  }

  private paintPerf(): void {
    const box = this.section('perf');
    box.replaceChildren();
    if (!this.symbol || !this.needBars(box)) return;
    const rows: PerfRow[] = performance(this.bars!, this.livePrice());
    const biggest = Math.max(1, ...rows.map((r) => Math.abs(r.pct ?? 0)));
    const table = el('div', 'perf');
    for (const r of rows) {
      const line = el('div', 'perf-row');
      const bar = el('span', 'perf-bar');
      const fill = el('i', dirOf(r.pct));
      fill.style.width = `${r.pct === null ? 0 : (Math.abs(r.pct) / biggest) * 100}%`;
      bar.append(fill);
      line.append(el('span', 'k', r.key), bar, el('span', `v ${dirOf(r.pct)}`, r.pct === null ? dash : pct(r.pct)));
      if (r.pct === null) line.title = 'the stored prices do not go back that far';
      table.append(line);
    }
    box.append(table);
  }

  private paintTech(): void {
    const box = this.section('tech');
    box.replaceChildren();
    if (!this.symbol) return;
    const frames = el('div', 'det-frames');
    for (const f of FRAMES) {
      const b = el('button', `btn${f === this.frame ? ' on' : ''}`, f);
      b.addEventListener('click', () => {
        this.frame = f;
        this.paintTech();
      });
      frames.append(b);
    }
    box.append(frames);
    if (!this.needBars(box)) return;
    const t = technicals(frameBars(this.bars!, this.frame));
    if (!t) return void box.append(this.note('not enough history on this timeframe'));
    const wrap = el('div', 'gauge');
    wrap.append(gaugeSvg(t.summary.score), el('div', `gauge-label ${verdictClass(t.summary.label)}`, t.summary.label));
    box.append(wrap);
    const counts = (name: string, s: Summary) => {
      const line = el('div', 'tech-row');
      line.append(el('span', 'k', name), el('span', 'dn', String(s.sell)), el('span', 'flat', String(s.neutral)), el('span', 'up', String(s.buy)), el('span', `v ${verdictClass(s.label)}`, s.label));
      return line;
    };
    const head = el('div', 'tech-row tech-head');
    head.append(el('span', 'k', ''), el('span', 'dn', 'Sell'), el('span', 'flat', 'Neutral'), el('span', 'up', 'Buy'), el('span', 'v', ''));
    box.append(head, counts('Oscillators', t.oscillatorSummary), counts('Summary', t.summary), counts('Averages', t.averageSummary));
    const list = (title: string, votes: Vote[]) => {
      const d = el('details', 'tech-list');
      d.append(el('summary', '', `${title} (${votes.length})`));
      for (const v of votes) {
        const line = el('div', 'tech-item');
        line.append(el('span', 'k', v.name), el('span', 'num', v.value === null ? dash : price(v.value)), el('span', `v ${v.verdict === 'buy' ? 'up' : v.verdict === 'sell' ? 'dn' : 'flat'}`, v.verdict));
        d.append(line);
      }
      return d;
    };
    box.append(list('Oscillators', t.oscillators), list('Moving averages', t.averages));
    box.append(this.note('rules are listed in the TRD (technicals gauge); the dial is our reading of the common definitions'));
  }

  private paintSeas(): void {
    const box = this.section('seas');
    box.replaceChildren();
    if (!this.symbol || !this.needBars(box)) return;
    const s = seasonals(this.bars!);
    if (!s) return void box.append(this.note('needs at least 2 full years of prices'));
    box.append(seasonalsSvg(s));
    const cells = el('div', 'months');
    const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    for (const m of s.months) {
      const c = el('div', `month ${dirOf(m.avg)}`);
      c.append(el('span', 'k', names[m.month]), el('span', 'v', m.avg === null ? dash : `${m.avg > 0 ? '+' : ''}${m.avg.toFixed(1)}%`));
      c.title = m.n ? `${names[m.month]}: average ${m.avg?.toFixed(2)}%, up in ${Math.round((m.up ?? 0) * m.n)} of ${m.n} years` : 'no data';
      cells.append(c);
    }
    box.append(cells, this.note(`${s.years.length} years drawn (this year bold, the dashed line is the average of full years); cells are average monthly returns`));
  }

  dispose(): void {
    this.offs.forEach((off) => off());
    super.dispose();
  }
}
