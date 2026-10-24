import { readVerdict } from '../verdictdata';
import { decisionAndCouncil } from '../verdictview';
import { startVerdict, watchVerdictJob } from '../verdictjob';
import { loadAnalysis, type AnalysisData } from './analysis';
import { el, Panel, type AppContext } from './base';

/** The stored verdict for the linked symbol, with each analyst's call; a button makes a fresh one (about 30 seconds, uses the language model). The Research page shows the same data in full. */
export class VerdictPanel extends Panel {
  private content = el('div', 'content');
  private token = 0;
  private stop: (() => void) | null = null;
  private busy = '';

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.start();
  }

  protected build(): void {
    this.body.append(this.content);
    this.content.textContent = 'pick a symbol';
  }

  protected symbolChanged(symbol: string): void {
    this.setTitle(`Verdict · ${symbol}`);
    void this.refresh();
  }

  private async refresh(): Promise<void> {
    const mine = ++this.token;
    const symbol = this.symbol;
    this.content.textContent = 'loading…';
    const d = await loadAnalysis(symbol);
    if (mine !== this.token || this.disposed) return;
    this.draw(d);
  }

  private draw(d: AnalysisData | null): void {
    this.content.replaceChildren();
    if (!d) return void (this.content.textContent = 'the warehouse is busy (a build is running); try again in a minute');
    const v = readVerdict(d);
    if (!v) {
      this.content.append(el('div', 'note', `No stored verdict for ${d.symbol}. Verdicts are made on request and kept.`));
    } else {
      const head = el('div', 'vhead');
      head.append(el('span', `chip ${v.tone}`, v.band), el('span', 'vconv', `conviction ${v.conviction ?? '—'}${v.raw !== null ? ` (raw ${v.raw})` : ''}`));
      this.content.append(head);
      if (v.calibration) this.content.append(el('div', 'note', `${v.calibration}${v.trust !== null ? ` · trust ${v.trust}` : ''}: conviction is scaled down until the analysts beat a plain baseline in tests.`));
      this.content.append(el('div', 'note', `Made ${v.madeAt.slice(0, 16)} from data as of ${v.dataAsOf}${v.newerDays > 0 ? ` (${v.newerDays} days of newer prices exist)` : ''}.`));
      const list = el('div', 'analysts');
      for (const a of v.analysts) {
        const row = el('div', 'analyst');
        const top = el('div', 'atop');
        top.append(el('span', 'an', a.name), el('span', `chip ${a.tone}`, a.signal), el('span', 'ac', `${a.confidence}% · ${a.coverage}`));
        const why = el('div', 'why', a.reasoning);
        why.hidden = true;
        top.addEventListener('click', () => (why.hidden = !why.hidden));
        row.append(top, why);
        list.append(row);
      }
      this.content.append(list);
      this.content.append(...decisionAndCouncil(v));
      for (const r of v.risks) this.content.append(el('div', 'flagline', `⚠ ${r}`));
      for (const s of v.skipped) this.content.append(el('div', 'note', `${s.name} was not run${s.reason ? `: ${s.reason}` : ''}`));
    }
    const actions = el('div', 'actions');
    const make = el('button', 'btn', v ? 'Make a fresh verdict' : 'Make a verdict');
    make.title = 'Runs the analysts on the stored data. About 30 seconds; it calls the language model.';
    make.disabled = this.busy !== '';
    make.addEventListener('click', () => void this.make(d.symbol));
    const open = el('button', 'btn', 'Open full analysis');
    open.title = 'Open the Research page: every analyst in full, plus the fundamentals';
    open.addEventListener('click', () => this.ctx.openResearch(d.symbol));
    actions.append(make, open);
    this.content.append(actions);
    if (this.busy) this.content.append(el('div', 'note', `Making a verdict for ${this.busy}… (about 30 seconds)`));
  }

  private async make(symbol: string): Promise<void> {
    const start = await startVerdict(symbol);
    if (start === 'busy') return void this.content.append(el('div', 'flagline', 'Another verdict is already being made; try again when it finishes.'));
    if (start === 'failed') return void this.content.append(el('div', 'flagline', 'The verdict could not be started.'));
    this.busy = symbol;
    void this.refresh();
    this.stop = watchVerdictJob(async (end) => {
      this.stop = null;
      this.busy = '';
      await this.refresh();
      if (end.state === 'failed') this.content.append(el('div', 'flagline', end.message ?? 'The verdict failed.'));
    });
  }

  dispose(): void {
    this.stop?.();
    super.dispose();
  }
}
