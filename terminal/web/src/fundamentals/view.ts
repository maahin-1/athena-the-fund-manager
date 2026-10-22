import { attachSuggest } from '../suggest';
import type { Quote } from '../feed';
import { pct, price, signed } from '../format';
import { el, type AppContext } from '../panels/base';
import { makePriceChart, type PriceChart } from './chartbox';
import { periodLabel } from './format';
import type { DocLink, FundPage } from './model';
import { industryNotes, industryTable } from './industry';
import { PeerBox } from './peerbox';
import { growthBlocks, keyBlocks, tableFrom, type Table } from './tables';

const LINK_GROUP = 'blue' as const; // the page follows the blue link group, like the Research page
const SECTIONS = ['Peers', 'Quarterly Results', 'Profit & Loss', 'Balance Sheet', 'Cash Flows', 'Ratios', 'Industry', 'Shareholding', 'Documents'] as const;
type Basis = 'consolidated' | 'standalone';

export interface ViewOptions {
  makeChart?: () => PriceChart;
}

const ARCHIVE = 'https://nsearchives.nseindia.com/';

/** A link opens only when it is on the exchange's archive host; anything else is shown as plain text. */
export function safeHref(url: string): string | null {
  return url.startsWith(ARCHIVE) && /^[\w./:%+~@=&-]+$/.test(url) ? url : null;
}

const docCount = (page: FundPage): number => {
  const d = page.documents;
  return d ? d.annual_reports.length + d.concalls.length + d.announcements.length : 0;
};

function docList(title: string, docs: DocLink[], label: (d: DocLink) => string): HTMLElement {
  const box = el('div', 'fdoc-box');
  box.append(el('div', 'fg-title', title));
  if (!docs.length) box.append(el('div', 'note', 'none stored'));
  for (const d of docs) {
    const row = el('div', 'fdoc');
    const href = safeHref(d.url);
    const text = label(d);
    if (href) {
      const a = el('a', '', text);
      a.href = href;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      row.append(a);
    } else row.append(el('span', '', text));
    row.append(el('span', 'note', d.size ? `${d.date} · ${d.size}` : d.date));
    box.append(row);
  }
  return box;
}

function drawTable(table: Table): HTMLElement {
  const wrap = el('div', 'ftable-wrap');
  const t = el('table', 'ftable');
  const head = el('tr');
  head.append(el('th', '', ''), ...table.head.map((h) => el('th', '', h)));
  const thead = el('thead');
  thead.append(head);
  t.append(thead);
  const body = el('tbody');
  for (const row of table.rows) {
    const tr = el('tr');
    const label = el('td', 'flabel', row.label);
    if (row.how) {
      label.title = row.how;
      label.classList.add('has-how');
    }
    tr.append(label, ...row.cells.map((c) => el('td', c.cls, c.text)));
    body.append(tr);
  }
  t.append(body);
  wrap.append(t);
  return wrap;
}

/** One company the way the Screener's company page lays it out: key figures, price chart, quarters, P&L with growth boxes, balance sheet, cash flows
 *  and ratios, from `/api/fund/:symbol`. Needs no market data except the live price in the header. */
export class FundamentalsView {
  readonly element = el('div', 'fund');
  private list = el('div', 'flist');
  private main = el('div', 'fmain');
  private head = el('div', 'ftitle');
  private body = el('div', 'fbody');
  private picker = el('select', 'fpick');
  private searchBox = el('input', 'cmd fsearch');
  private fetched = ''; // a company whose filings were just fetched, so an empty result is explained rather than offered again
  private livePrice = el('span', 'fprice');
  private liveChange = el('span', 'fchg');
  private chart: PriceChart;
  private peerBox: PeerBox;
  private pendingSection: string | null = null; // a section to scroll to once the page is drawn
  private symbol = '';
  private basis: Basis = 'consolidated';
  private token = 0;
  private visible = false;
  private page: FundPage | null = null;
  private offQuotes: (() => void) | null = null;
  private offLink: (() => void) | null = null;
  private offList: (() => void) | null = null;

  constructor(private ctx: AppContext, options: ViewOptions = {}) {
    this.element.hidden = true;
    this.chart = (options.makeChart ?? makePriceChart)();
    this.peerBox = new PeerBox(ctx);
    this.picker.setAttribute('aria-label', 'pick a stock');
    this.searchBox.placeholder = 'Search a company…';
    this.searchBox.setAttribute('aria-label', 'search company');
    attachSuggest(this.searchBox, {
      kinds: 'stock',
      enterTakesBest: true,
      onPick: (hit) => {
        this.searchBox.value = '';
        this.pick(hit.s);
      },
    });
    this.picker.addEventListener('change', () => this.pick(this.picker.value));
    this.main.append(this.head, this.body);
    this.element.append(this.list, this.main);
    this.offQuotes = ctx.feed.onQuotes((qs) => this.onQuotes(qs));
    this.offLink = ctx.bus.subscribe(LINK_GROUP, (s) => this.follow(s));
    this.offList = ctx.watchlist.subscribe(() => this.paintList());
    this.paintList();
  }

  show(symbol?: string, section?: string): void {
    this.pendingSection = section ?? null;
    this.visible = true;
    this.element.hidden = false;
    const next = symbol || this.ctx.bus.symbolOf(LINK_GROUP) || this.symbol || this.ctx.watchlist.items[0] || '';
    if (next) this.follow(next, true);
  }

  hide(): void {
    this.visible = false;
    this.element.hidden = true;
    this.ctx.feed.release('fund');
  }

  private pick(symbol: string): void {
    this.follow(symbol);
    this.ctx.bus.setSymbol(LINK_GROUP, symbol);
  }

  private follow(symbol: string, force = false): void {
    if (!this.visible || !symbol || (symbol === this.symbol && !force)) return;
    this.symbol = symbol;
    this.paintList();
    this.ctx.feed.need('fund', [symbol]);
    void this.load();
  }

  private paintList(): void {
    this.list.replaceChildren(this.searchBox, el('div', 'flisthead', 'Watchlist'));
    this.picker.replaceChildren();
    const items = this.ctx.watchlist.items.includes(this.symbol) || !this.symbol ? this.ctx.watchlist.items : [this.symbol, ...this.ctx.watchlist.items];
    for (const s of items) {
      const b = el('button', 'fitem', s);
      b.dataset.symbol = s;
      b.classList.toggle('on', s === this.symbol);
      b.addEventListener('click', () => this.pick(s));
      this.list.append(b);
      this.picker.append(new Option(s, s));
    }
    this.picker.value = this.symbol;
  }

  private async fetchPage(symbol: string, basis: Basis): Promise<{ page?: FundPage; status: number; error?: string }> {
    try {
      const r = await fetch(`/api/fund/${encodeURIComponent(symbol)}?basis=${basis}`);
      if (r.ok) return { page: (await r.json()) as FundPage, status: r.status };
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      return { status: r.status, error: body.error };
    } catch {
      return { status: 0, error: 'could not reach the server' };
    }
  }

  private async load(): Promise<void> {
    const mine = ++this.token;
    const symbol = this.symbol;
    this.head.replaceChildren(el('span', 'fsym', symbol), el('span', 'note', 'loading…'));
    this.body.replaceChildren();
    let got = await this.fetchPage(symbol, this.basis);
    let note = '';
    if (got.status === 404 && this.basis === 'consolidated') {
      const standalone = await this.fetchPage(symbol, 'standalone'); // some companies file standalone results only
      if (standalone.page) {
        got = standalone;
        note = 'Consolidated results are not stored for this company; showing standalone.';
      }
    }
    if (mine !== this.token) return;
    if (!got.page) return this.fail(symbol, got.status, got.error);
    this.render(got.page, note);
  }

  /** Nothing stored: offer to fetch it now (or follow a fetch that is already running). */
  private offerFetch(symbol: string): void {
    const note = el('div', 'note', `No statements are stored for ${symbol} yet.`);
    const button = el('button', 'btn ffetch', 'Fetch now');
    button.title = 'Download the statements, shareholding and documents from the exchange (about a minute)';
    button.addEventListener('click', () => void this.startFetch(symbol));
    this.peerBox.show(symbol);
    this.body.replaceChildren(note, button, this.peerBox.element);
    this.scrollPending();
  }

  /** Scroll to the section a command asked for, once there is something to scroll to. */
  private scrollPending(): void {
    const name = this.pendingSection;
    if (!name) return;
    this.pendingSection = null;
    setTimeout(() => this.body.querySelector(`[data-section="${name}"]`)?.scrollIntoView({ block: 'start' }), 0);
  }

  private async startFetch(symbol: string): Promise<void> {
    this.body.replaceChildren(el('div', 'note ffetching', 'starting…'));
    try {
      const r = await fetch(`/api/fund-fetch/${encodeURIComponent(symbol)}`, { method: 'POST' });
      if (r.status === 409) {
        const other = ((await r.json().catch(() => ({}))) as { symbol?: string }).symbol ?? 'another company';
        this.body.replaceChildren(el('div', 'note bad', `The exchange is being asked for ${other} right now; try again when that finishes.`));
        return this.addRetry(symbol);
      }
      if (!r.ok && r.status !== 202) throw new Error(`HTTP ${r.status}`);
    } catch {
      this.body.replaceChildren(el('div', 'note bad', 'The fetch could not be started.'));
      return this.addRetry(symbol);
    }
    void this.followFetch(symbol);
  }

  private addRetry(symbol: string): void {
    const again = el('button', 'btn ffetch', 'Try again');
    again.addEventListener('click', () => void this.startFetch(symbol));
    this.body.append(again);
  }

  /** Show the running fetch's step until it ends, then load the page (only while this company is still the one on show). */
  private async followFetch(symbol: string): Promise<void> {
    for (;;) {
      if (this.symbol !== symbol) return; // the person moved on; the fetch carries on and the page is there when they come back
      let status: { state: string; symbol?: string; stage?: string; step?: number; of?: number; error?: string } = { state: 'idle' };
      try {
        status = await (await fetch('/api/fund-fetch')).json();
      } catch {
        /* the server may be busy; look again */
      }
      if (status.symbol === symbol && status.state === 'running') {
        this.body.replaceChildren(el('div', 'note ffetching', `${status.stage ?? 'fetching'}… (step ${status.step} of ${status.of}); this takes about a minute`));
      } else if (status.symbol === symbol && status.state === 'done') {
        this.fetched = symbol;
        return void this.load();
      } else if (status.symbol === symbol && status.state === 'failed') {
        this.body.replaceChildren(el('div', 'note bad', `${status.error ?? 'The fetch failed.'}`));
        return this.addRetry(symbol);
      } else if (status.state === 'idle' || status.symbol !== symbol) {
        return this.offerFetch(symbol); // nothing running for this company
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  private fail(symbol: string, status: number, error?: string): void {
    this.head.replaceChildren(el('span', 'fsym', symbol));
    if (status === 404 && this.fetched === symbol) {
      this.fetched = '';
      this.body.replaceChildren(el('div', 'note bad', `The exchange has no financial results for ${symbol} in the form this page reads (it may be an ETF, an SME or a very recent listing).`));
      return;
    }
    if (status === 404) {
      this.offerFetch(symbol);
      void fetch('/api/fund-fetch').then(async (r) => {
        const s = (await r.json()) as { state?: string; symbol?: string };
        if (s.state === 'running' && s.symbol === symbol && this.symbol === symbol) void this.followFetch(symbol);
      }).catch(() => undefined);
      return;
    }
    const text =
      status === 404
        ? ''
        : status === 503
          ? 'The statements store is busy (an ingest is writing to it); try again in a moment.'
          : (error ?? 'The statements could not be loaded.');
    this.body.replaceChildren(el('div', 'note bad', text));
  }

  /** Draw a page document. Public so tests can feed one in without a server. */
  render(page: FundPage, note = ''): void {
    this.page = page;
    this.basis = page.basis;
    this.symbol = page.symbol;
    this.head.replaceChildren(this.headerRow(page), this.navRow(page));
    this.body.replaceChildren();
    if (note) this.body.append(el('div', 'note', note));
    this.peerBox.show(page.symbol);
    this.body.append(this.keyGrid(page), this.chartCard(page), this.peerBox.element);
    this.body.append(
      this.section('Quarterly Results', drawTable(tableFrom(page.quarters.ends, page.quarters.rows)), page.quarters.rows.length > 0),
      this.section('Profit & Loss', this.plBlock(page), page.annual.income.length > 0),
      this.section('Balance Sheet', drawTable(tableFrom(page.annual.ends, page.annual.balance)), page.annual.balance.length > 0),
      this.section('Cash Flows', drawTable(tableFrom(page.annual.ends, page.annual.cashflow)), page.annual.cashflow.length > 0),
      this.section('Ratios', drawTable(tableFrom(page.ratios.ends, [...page.ratios.rows, ...page.annual.banking])), page.ratios.rows.length + page.annual.banking.length > 0),
      ...(page.industry?.rows.length ? [this.section('Industry', this.industryBlock(page), true)] : []),
      this.section('Shareholding', drawTable(tableFrom(page.shareholding?.ends ?? [], page.shareholding?.rows ?? [])), (page.shareholding?.rows.length ?? 0) > 0),
    );
    this.body.append(this.section('Documents', this.documents(page), docCount(page) > 0), this.footer(page));
    this.paintLive(this.ctx.feed.quotes.get(page.symbol));
    this.paintList();
    this.scrollPending();
  }

  private headerRow(page: FundPage): HTMLElement {
    const row = el('div', 'ftop');
    const toggle = el('span', 'basis-toggle');
    for (const b of ['consolidated', 'standalone'] as const) {
      const button = el('button', `rbtn${page.basis === b ? ' on' : ''}`, b === 'consolidated' ? 'Consolidated' : 'Standalone');
      button.dataset.basis = b;
      button.disabled = !page.bases.includes(b);
      button.title = button.disabled ? `${b} statements are not stored for ${page.symbol}` : `Show the ${b} statements`;
      button.addEventListener('click', () => {
        this.basis = b;
        void this.load();
      });
      toggle.append(button);
    }
    const exportLink = el('a', 'btn fexport', 'Export to Excel');
    exportLink.href = `/api/fund/${encodeURIComponent(page.symbol)}/export?basis=${page.basis}`;
    exportLink.setAttribute('download', `${page.symbol}-${page.basis}.xlsx`);
    exportLink.title = 'Download these statements as an Excel workbook';
    const chartLink = el('button', 'btn', 'Chart');
    chartLink.addEventListener('click', () => this.ctx.showSymbol(page.symbol));
    const research = el('button', 'btn', 'Research');
    research.addEventListener('click', () => this.ctx.openResearch(page.symbol));
    row.append(this.picker, el('span', 'fsym', page.symbol), el('span', 'fname', page.name), this.livePrice, this.liveChange, toggle, exportLink, chartLink, research);
    this.picker.value = page.symbol;
    return row;
  }

  private navRow(page: FundPage): HTMLElement {
    const nav = el('div', 'fnav');
    for (const name of SECTIONS) {
      if (name === 'Industry' && !page.industry?.rows.length) continue;
      const b = el('button', 'fnavbtn', name);
      b.addEventListener('click', () => this.body.querySelector(`[data-section="${name}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
      nav.append(b);
    }
    return nav;
  }

  private keyGrid(page: FundPage): HTMLElement {
    const grid = el('div', 'fkeys');
    for (const k of keyBlocks(page)) {
      const box = el('div', 'fkey');
      box.title = k.how;
      box.append(el('span', 'fk-label', k.label), el('span', 'fk-value', k.text));
      grid.append(box);
    }
    if (!page.key.length) grid.append(el('div', 'note', 'Key figures need a price; none is stored for this symbol.'));
    return grid;
  }

  private chartCard(page: FundPage): HTMLElement {
    const card = el('section', 'fcard fchart-card');
    card.append(el('h3', '', 'Price'), this.chart.element);
    this.chart.load(page.symbol);
    return card;
  }

  private plBlock(page: FundPage): HTMLElement {
    const box = el('div');
    box.append(drawTable(tableFrom(page.annual.ends, page.annual.income, { ttm: true })));
    const growth = el('div', 'fgrowth');
    for (const g of growthBlocks(page.growth)) {
      const card = el('div', 'fgrow');
      card.append(el('div', 'fg-title', g.title));
      for (const r of g.rows) {
        const line = el('div', 'fg-row');
        line.append(el('span', '', `${r.label}:`), el('span', r.cls, r.text));
        card.append(line);
      }
      growth.append(card);
    }
    box.append(growth);
    return box;
  }

  private section(name: string, content: HTMLElement, present: boolean): HTMLElement {
    const card = el('section', 'fcard wide');
    card.dataset.section = name;
    card.append(el('h3', '', name), present ? content : el('div', 'note', `No ${name.toLowerCase()} are stored for this company yet.`));
    return card;
  }

  private industryBlock(page: FundPage): HTMLElement {
    const box = el('div', 'findustry');
    if (!page.industry) return box;
    box.append(drawTable(industryTable(page.industry)));
    for (const line of industryNotes(page.industry)) box.append(el('div', 'note', line));
    box.append(el('div', 'note', 'Industry figures are totals across the industry (market value over earnings, not an average of company ratios), so a few near-zero earnings do not distort them.'));
    return box;
  }

  private documents(page: FundPage): HTMLElement {
    const docs = page.documents ?? { annual_reports: [], concalls: [], announcements: [] };
    const grid = el('div', 'fdocs');
    grid.append(
      docList('Annual reports', docs.annual_reports, (d) => d.title),
      docList('Investor calls', docs.concalls, (d) => d.title),
      docList('Announcements', docs.announcements, (d) => `${d.category}: ${d.title}`),
    );
    return grid;
  }

  private footer(page: FundPage): HTMLElement {
    const foot = el('div', 'fnotes');
    const cov = page.coverage;
    foot.append(
      el('div', 'note', `Source: NSE results filings (XBRL). Rupee figures in crores. ${cov ? `${cov.periods} periods held, ${periodLabel(cov.first)} to ${periodLabel(cov.last)}.` : ''}`),
    );
    for (const gap of page.gaps) foot.append(el('div', 'note', `Not held yet: ${gap}.`));
    for (const h of page.history ?? []) foot.append(el('div', 'note fhistory', `Includes ${h.symbol} (${periodLabel(h.first)} to ${periodLabel(h.last)}): ${h.note}`));
    if (page.price !== null) foot.append(el('div', 'note', `Price used for market figures: ${price(page.price)}${page.price_note ? ` (${page.price_note})` : ''}.`));
    foot.append(el('div', 'note', 'Hover a ratio or key figure to see how it is worked out.'));
    return foot;
  }

  private paintLive(q: Quote | undefined): void {
    if (!q) {
      this.livePrice.textContent = '';
      this.liveChange.textContent = '';
      return;
    }
    const change = q.l - q.pc;
    this.livePrice.textContent = price(q.l);
    this.liveChange.textContent = `${signed(change)} (${pct(q.pc ? (change / q.pc) * 100 : 0)})`;
    this.liveChange.className = `fchg ${change > 0 ? 'up' : change < 0 ? 'dn' : 'flat'}`;
  }

  private onQuotes(quotes: Quote[]): void {
    if (!this.visible) return;
    const q = quotes.find((x) => x.s === this.symbol);
    if (q) this.paintLive(q);
  }

  dispose(): void {
    this.offQuotes?.();
    this.offLink?.();
    this.offList?.();
    this.chart.dispose();
    this.ctx.feed.release('fund');
  }

  /** The document now on screen (for tests). */
  get current(): FundPage | null {
    return this.page;
  }
}
