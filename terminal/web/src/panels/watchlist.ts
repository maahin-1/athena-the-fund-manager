import type { Quote } from '../feed';
import { pct, price, signed } from '../format';
import { el, Panel, type AppContext } from './base';

const INDICES = ['NIFTY 50', 'NIFTY BANK'];

interface Row {
  tr: HTMLTableRowElement;
  last: HTMLElement;
  chg: HTMLElement;
  chgPct: HTMLElement;
  flag: HTMLElement;
  prev: number | null;
}

/** Symbol, Last, Chg, Chg% with collapsible Indices and Stocks sections. Clicking a row moves every panel linked to this one. */
export class WatchlistPanel extends Panel {
  private rows = new Map<string, Row>();
  private offStore: (() => void) | null = null;
  private closed = new Set<string>();

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.titleEl.textContent = 'Watchlist';
    this.start();
  }

  protected build(): void {
    this.offStore = this.ctx.watchlist.subscribe(() => this.render());
    this.render();
  }

  private render(): void {
    this.body.replaceChildren();
    this.rows.clear();
    const table = el('table', 'wl');
    const head = el('tr', 'wl-head');
    for (const t of ['Symbol', 'Last', 'Chg', 'Chg%']) head.append(el('th', '', t));
    table.append(head);
    this.section(table, 'Indices', INDICES, false);
    this.section(table, 'Stocks', this.ctx.watchlist.items, true);
    this.body.append(table);
    this.ctx.feed.need(this.id, this.ctx.watchlist.items);
    for (const q of this.ctx.feed.quotes.values()) this.paint(q, false);
    INDICES.forEach((name) => void this.loadIndex(name));
    this.select(this.symbol);
  }

  private section(table: HTMLTableElement, title: string, symbols: string[], removable: boolean): void {
    const header = el('tr', 'wl-section');
    const cell = el('td', '', `${this.closed.has(title) ? '▸' : '▾'} ${title}`);
    cell.colSpan = 4;
    header.append(cell);
    header.addEventListener('click', () => {
      if (!this.closed.delete(title)) this.closed.add(title);
      this.render();
    });
    table.append(header);
    if (this.closed.has(title)) return;
    for (const symbol of symbols) {
      const tr = el('tr', 'wl-row');
      tr.dataset.symbol = symbol;
      const name = el('td', 'sym');
      const flag = el('i', 'src');
      name.append(flag, document.createTextNode(symbol));
      if (removable) {
        const x = el('button', 'x', '×');
        x.title = `Remove ${symbol}`;
        x.addEventListener('click', (e) => {
          e.stopPropagation();
          this.ctx.watchlist.remove(symbol);
        });
        name.append(x);
      }
      const row: Row = { tr, last: el('td', 'num', '—'), chg: el('td', 'num', ''), chgPct: el('td', 'num', ''), flag, prev: null };
      tr.append(name, row.last, row.chg, row.chgPct);
      tr.addEventListener('click', () => {
        this.select(symbol);
        this.pick(symbol);
      });
      this.rows.set(symbol, row);
      table.append(tr);
    }
  }

  private select(symbol: string): void {
    this.rows.forEach((row, s) => row.tr.classList.toggle('selected', s === symbol));
  }

  protected symbolChanged(symbol: string): void {
    this.select(symbol);
  }

  private async loadIndex(name: string): Promise<void> {
    try {
      const r = await fetch(`/api/bars?symbol=${encodeURIComponent(name)}&tf=1D&limit=2`);
      const body = (await r.json()) as { bars: { c: number }[] };
      const [prev, last] = body.bars.slice(-2);
      const row = this.rows.get(name);
      if (row && prev && last) this.fill(row, last.c, last.c - prev.c, ((last.c - prev.c) / prev.c) * 100, false);
    } catch {
      /* the server may be restarting; the row keeps its dash */
    }
  }

  protected onQuotes(quotes: Quote[]): void {
    for (const q of quotes) this.paint(q, true);
  }

  private paint(q: Quote, flash: boolean): void {
    const row = this.rows.get(q.s);
    if (!row) return;
    const change = q.l - q.pc;
    this.fill(row, q.l, change, q.pc ? (change / q.pc) * 100 : 0, flash);
    row.flag.dataset.src = q.src;
    row.flag.title = q.src === 'stream' ? 'live stream' : 'polled from the exchange (delayed)';
  }

  private fill(row: Row, last: number, change: number, changePct: number, flash: boolean): void {
    row.last.textContent = price(last);
    row.chg.textContent = signed(change);
    row.chgPct.textContent = pct(changePct);
    const dir = change > 0 ? 'up' : change < 0 ? 'dn' : 'flat';
    row.chg.className = row.chgPct.className = `num ${dir}`;
    if (flash && row.prev !== null && row.prev !== last) {
      const cls = last > row.prev ? 'flash-up' : 'flash-dn';
      row.last.classList.remove('flash-up', 'flash-dn');
      void row.last.offsetWidth; // restart the animation
      row.last.classList.add(cls);
    }
    row.prev = last;
  }

  dispose(): void {
    this.offStore?.();
    super.dispose();
  }
}
