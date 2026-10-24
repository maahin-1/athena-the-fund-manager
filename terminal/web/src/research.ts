import type { Quote } from './feed';
import { pct, price, signed } from './format';
import { renderBacktest, runBacktest } from './backtestview';
import { serverStrategiesIO } from './formula/strategies';
import type { StrategyTree } from './formula/strategy';
import { StrategyBuilder } from './strategybuilder';
import { analyseLive, renderLive, type LiveView } from './live';
import { loadAnalysis, overviewView, packetView, type AnalysisData } from './panels/analysis';
import { el, type AppContext } from './panels/base';
import { readVerdict, type Analyst, type VerdictFacts } from './verdictdata';
import { decisionAndCouncil } from './verdictview';
import { startVerdict, watchVerdictJob } from './verdictjob';

const SECTIONS = ['Verdict', 'Analysts', 'Overview', 'Fundamentals', 'Technicals'] as const;
const LINK_GROUP = 'blue' as const; // the Research page follows the blue link group, like the default panels

/** A full page for one stock: the verdict, every analyst's reasoning in full, and the stored fundamentals and technicals. Needs no market data. */
export class ResearchView {
  readonly element = el('div', 'research');
  private list = el('div', 'rlist');
  private main = el('div', 'rmain');
  private title = el('div', 'rtitle');
  private body = el('div', 'rbody');
  private nav = el('div', 'rnav');
  private picker = el('select', 'rpick');
  private liveBox = el('div', 'livebar');
  private liveInput = el('input', 'cmd');
  private liveButton = el('button', 'btn', 'Analyse live');
  private liveStatus = el('span', 'note');
  private liveSlot = el('div', 'liveslot');
  private liveViews = new Map<string, LiveView>();
  private liveTimer: ReturnType<typeof setInterval> | null = null;
  private liveBusy = false;
  private btButton = el('button', 'btn', 'Backtest');
  private btSlot = el('div', 'liveslot');
  private btDispose: (() => void) | null = null;
  private builder = new StrategyBuilder(serverStrategiesIO(), (tree) => void this.runBt(tree));
  private symbol = '';
  private token = 0;
  private stopJob: (() => void) | null = null;
  private busy = '';
  private visible = false;
  private livePrice = el('span', 'rprice');
  private liveChange = el('span', 'rchg');
  private offQuotes: (() => void) | null = null;
  private offLink: (() => void) | null = null;
  private offList: (() => void) | null = null;

  constructor(private ctx: AppContext) {
    this.element.hidden = true;
    this.picker.setAttribute('aria-label', 'pick a stock');
    this.picker.addEventListener('change', () => this.pick(this.picker.value));
    for (const name of SECTIONS) {
      const b = el('button', 'rnavbtn', name);
      b.dataset.section = name;
      b.addEventListener('click', () => this.body.querySelector(`[data-section="${name}"]`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
      this.nav.append(b);
    }
    this.liveInput.placeholder = 'ticker, ISIN or company name';
    this.liveInput.setAttribute('aria-label', 'analyse a name live');
    this.liveButton.title = 'Runs every analyst now (including news) and the risk and valuation panels, as the Streamlit Analyse tab did. It calls the language model and takes one to two minutes.';
    this.liveInput.addEventListener('keydown', (e) => e.key === 'Enter' && void this.runLive(this.liveInput.value.trim(), true));
    this.liveButton.addEventListener('click', () => void this.runLive(this.liveInput.value.trim() || this.symbol, Boolean(this.liveInput.value.trim())));
    this.btButton.title = 'Replays the built-in rules day by day over up to eight years and compares each with buy and hold. Takes a few seconds to a minute.';
    this.btButton.addEventListener('click', () => void this.runBt());
    this.liveBox.append(this.liveInput, this.liveButton, this.btButton, this.liveStatus);
    this.main.append(this.title, this.nav, this.liveBox, this.builder.element, this.liveSlot, this.btSlot, this.body);
    this.element.append(this.list, this.main);
    this.offQuotes = ctx.feed.onQuotes((qs) => this.onQuotes(qs));
    this.offLink = ctx.bus.subscribe(LINK_GROUP, (s) => this.follow(s));
    this.offList = ctx.watchlist.subscribe(() => this.paintList());
    this.paintList();
  }

  /** Show the page, on `symbol` when given, else on the symbol the blue group last chose, else the first stock in the watchlist. */
  show(symbol?: string): void {
    this.visible = true;
    this.element.hidden = false;
    const next = symbol || this.ctx.bus.symbolOf(LINK_GROUP) || this.symbol || this.ctx.watchlist.items[0] || '';
    if (next) this.follow(next, true);
  }

  hide(): void {
    this.visible = false;
    this.element.hidden = true;
    this.ctx.feed.release('research');
  }

  get shown(): boolean {
    return this.visible;
  }

  /** The user picked a stock here: tell the other panels too. */
  private pick(symbol: string): void {
    this.follow(symbol);
    this.ctx.bus.setSymbol(LINK_GROUP, symbol);
  }

  private follow(symbol: string, force = false): void {
    if (!this.visible || !symbol || (symbol === this.symbol && !force)) return;
    this.symbol = symbol;
    this.builder.setSymbol(symbol);
    this.paintList();
    this.ctx.feed.need('research', [symbol]);
    void this.load();
  }

  private paintList(): void {
    this.list.replaceChildren(el('div', 'rlisthead', 'Watchlist'));
    this.picker.replaceChildren();
    const items = this.ctx.watchlist.items.includes(this.symbol) || !this.symbol ? this.ctx.watchlist.items : [this.symbol, ...this.ctx.watchlist.items];
    for (const s of items) {
      const b = el('button', 'ritem', s);
      b.dataset.symbol = s;
      b.classList.toggle('on', s === this.symbol);
      b.addEventListener('click', () => this.pick(s));
      this.list.append(b);
      this.picker.append(new Option(s, s));
    }
    this.picker.value = this.symbol;
  }

  /** Run the live analysis. A typed name may list related instruments; the current symbol and a picked candidate are analysed as they are. */
  private async runLive(query: string, relatives: boolean): Promise<void> {
    if (!query || this.liveBusy) return;
    this.liveBusy = true;
    this.liveButton.disabled = true;
    const started = Date.now();
    const tick = () => {
      this.liveStatus.textContent = `Asking the specialists about ${query}… ${Math.round((Date.now() - started) / 1000)} s (one to two minutes; the first run also starts the analysis service)`;
    };
    tick();
    this.liveTimer = setInterval(tick, 1000);
    const result = await analyseLive(query, relatives);
    if (this.liveTimer) clearInterval(this.liveTimer);
    this.liveTimer = null;
    this.liveBusy = false;
    this.liveButton.disabled = false;
    if (!result.ok) {
      this.liveStatus.textContent = result.error;
      this.liveStatus.className = 'note bad';
      return;
    }
    this.liveStatus.className = 'note';
    this.liveStatus.textContent = `Done in ${Math.round((Date.now() - started) / 1000)} s.`;
    const view = result.view;
    if (view.identifier) this.liveViews.set(view.identifier, view);
    this.liveInput.value = '';
    if (view.status !== 'needs_clarification' && view.identifier && view.identifier !== this.symbol) this.pick(view.identifier);
    this.showLive(view);
  }

  private async runBt(strategy?: StrategyTree): Promise<void> {
    const symbol = this.symbol;
    if (!symbol || this.liveBusy) return;
    this.liveBusy = true;
    this.btButton.disabled = this.liveButton.disabled = true;
    this.liveStatus.className = 'note';
    this.liveStatus.textContent = strategy
      ? `Replaying "${strategy.name}" on ${symbol} day by day… (a few seconds to a minute; the first run also starts the analysis service)`
      : `Replaying ${symbol} day by day… (a few seconds to a minute; the first run also starts the analysis service)`;
    const result = await runBacktest(symbol, strategy);
    this.liveBusy = false;
    this.btButton.disabled = this.liveButton.disabled = false;
    if (!result.ok) {
      this.liveStatus.className = 'note bad';
      this.liveStatus.textContent = result.error;
      return;
    }
    this.liveStatus.textContent = 'Backtest done.';
    if (symbol !== this.symbol) return; // the user moved on while it ran
    this.btDispose?.();
    const card = renderBacktest(result.data);
    this.btDispose = card.dispose;
    this.btSlot.replaceChildren(card.element);
  }

  private showLive(view: LiveView | undefined): void {
    this.btDispose?.();
    this.btDispose = null;
    this.btSlot.replaceChildren();
    this.liveSlot.replaceChildren();
    if (view) this.liveSlot.append(renderLive(view, (id) => void this.runLive(id, false)));
  }

  private async load(): Promise<void> {
    const mine = ++this.token;
    const symbol = this.symbol;
    this.showLive(this.liveViews.get(symbol));
    this.title.replaceChildren(el('span', 'rsym', symbol), el('span', 'note', 'loading…'));
    this.body.replaceChildren();
    const d = await loadAnalysis(symbol);
    if (mine !== this.token) return;
    this.draw(d, symbol);
  }

  private draw(d: AnalysisData | null, symbol: string): void {
    this.body.replaceChildren();
    this.title.replaceChildren();
    const name = d?.screen?.name ?? d?.name ?? '';
    const head = el('div', 'rtop');
    head.append(this.picker, el('span', 'rsym', symbol), el('span', 'rname', `${name}${d?.screen?.sector ? ` · ${d.screen.sector}` : ''}`), this.livePrice, this.liveChange);
    this.title.append(head);
    this.paintLive(this.ctx.feed.quotes.get(symbol));
    if (!d) return void this.body.append(el('div', 'note', 'The warehouse is busy (a build is running); try again in a minute.'));
    const v = readVerdict(d);
    this.body.append(this.verdictCard(d, v));
    if (v) this.body.append(this.analystsSection(v.analysts));
    else this.body.append(this.section('Analysts', el('div', 'note', 'No analyst has looked at this stock yet. Press "Make a verdict" above.')));
    this.body.append(this.section('Overview', overviewView(d)), this.section('Fundamentals', packetView(d.fundamentals, true, symbol, 'fundamentals')), this.section('Technicals', packetView(d.technical, false, symbol, 'technical')));
    this.body.append(el('div', 'note', `Stored data as of ${d.market_day ?? 'unknown'} (the last loaded trading day). This page does not need the market to be open.`));
  }

  private section(name: string, content: HTMLElement, span = false): HTMLElement {
    const card = el('section', `rcard${span ? ' wide' : ''}`);
    card.dataset.section = name;
    card.append(el('h3', '', name), content);
    return card;
  }

  private verdictCard(d: AnalysisData, v: VerdictFacts | null): HTMLElement {
    const box = el('div', 'rverdict');
    if (v) {
      const top = el('div', 'vhead');
      top.append(el('span', `chip big ${v.tone}`, v.band), el('span', 'vconv', `conviction ${v.conviction ?? '—'}${v.raw !== null ? ` (raw ${v.raw})` : ''}`));
      box.append(top);
      if (v.calibration) box.append(el('div', 'note', `${v.calibration}${v.trust !== null ? ` · trust ${v.trust}` : ''}. Conviction is scaled down until the analysts beat a plain baseline in tests.`));
      box.append(el('div', 'note', `Made ${v.madeAt.slice(0, 16)} from data as of ${v.dataAsOf}${v.newerDays > 0 ? ` (${v.newerDays} days of newer prices exist)` : ''}.`));
      if (v.disagree.length) box.append(el('div', 'flagline', `The analysts disagree — ${v.disagree.join(' · ')}.`));
      box.append(...decisionAndCouncil(v));
      for (const r of v.risks) box.append(el('div', 'flagline', `⚠ ${r}`));
      for (const n of v.notes) box.append(el('div', 'note', n));
      for (const s of v.skipped) box.append(el('div', 'note', `${s.name} was not run${s.reason ? `: ${s.reason}` : ''}.`));
    } else {
      box.append(el('div', 'note', `No stored verdict for ${d.symbol}. Verdicts are made on request and kept.`));
    }
    const actions = el('div', 'actions');
    const make = el('button', 'btn', v ? 'Make a fresh verdict' : 'Make a verdict');
    make.title = 'Runs the analysts on the stored data. About 30 seconds; it calls the language model.';
    make.disabled = this.busy !== '';
    make.addEventListener('click', () => void this.make(d.symbol, box));
    actions.append(make);
    box.append(actions);
    if (this.busy) box.append(el('div', 'note', `Making a verdict for ${this.busy}… (about 30 seconds)`));
    return this.section('Verdict', box, true);
  }

  private analystsSection(analysts: Analyst[]): HTMLElement {
    const grid = el('div', 'agrid');
    for (const a of analysts) {
      const card = el('article', 'acard');
      card.dataset.analyst = a.name;
      const top = el('div', 'atop');
      top.append(el('span', 'an', a.name), el('span', `chip ${a.tone}`, a.signal));
      const meter = el('div', 'meter');
      const fill = el('div', `fill ${a.tone}`);
      fill.style.width = `${Math.max(0, Math.min(100, a.confidence))}%`;
      meter.append(fill);
      const sub = el('div', 'note', `confidence ${a.confidence}% · data ${a.coverage || 'unknown'}`);
      card.append(top, meter, sub, el('div', 'reason', a.reasoning || 'no reasoning was stored'));
      if (a.missing.length) card.append(el('div', 'note', `missing: ${a.missing.join(', ')}`));
      grid.append(card);
    }
    return this.section('Analysts', grid, true);
  }

  private async make(symbol: string, box: HTMLElement): Promise<void> {
    const start = await startVerdict(symbol);
    if (start === 'busy') return void box.append(el('div', 'flagline', 'Another verdict is already being made; try again when it finishes.'));
    if (start === 'failed') return void box.append(el('div', 'flagline', 'The verdict could not be started.'));
    this.busy = symbol;
    await this.load();
    this.stopJob = watchVerdictJob(async (end) => {
      this.stopJob = null;
      this.busy = '';
      await this.load();
      // the job may have been for a stock the user has since left; say nothing about it under another stock
      if (end.state === 'failed' && this.symbol === symbol) this.body.querySelector('.rverdict')?.append(el('div', 'flagline', end.message ?? 'The verdict failed.'));
    });
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
    this.liveChange.className = `rchg ${change > 0 ? 'up' : change < 0 ? 'dn' : 'flat'}`;
  }

  private onQuotes(quotes: Quote[]): void {
    if (!this.visible) return;
    const q = quotes.find((x) => x.s === this.symbol);
    if (q) this.paintLive(q);
  }

  dispose(): void {
    this.stopJob?.();
    this.offQuotes?.();
    this.offLink?.();
    this.offList?.();
    this.ctx.feed.release('research');
  }
}
