import { loadAnalysis, type AnalysisData } from './analysis';
import { el, Panel, type AppContext } from './base';

interface Specialist {
  signal?: string;
  confidence?: number;
  data_coverage?: string;
  reasoning?: string;
}

interface VerdictInner {
  verdict?: string;
  conviction?: number;
  raw_conviction?: number;
  calibration?: string;
  trust?: number;
  key_risks?: string[];
}

const BAND: Record<string, 'up' | 'dn' | 'flat'> = { 'Strong Buy': 'up', Buy: 'up', Overweight: 'up', Hold: 'flat', Neutral: 'flat', Underweight: 'dn', Sell: 'dn', 'Strong Sell': 'dn' };
const SIGNAL: Record<string, 'up' | 'dn' | 'flat'> = { bullish: 'up', bearish: 'dn', neutral: 'flat' };

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
}

/** The stored verdict for the linked symbol, with each analyst's call; a button makes a fresh one (about 30 seconds, uses the language model). */
export class VerdictPanel extends Panel {
  private content = el('div', 'content');
  private token = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
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
    this.titleEl.textContent = `Verdict · ${symbol}`;
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
    const v = d.verdict;
    if (!v) {
      this.content.append(el('div', 'note', `No stored verdict for ${d.symbol}. Verdicts are made on request and kept.`));
    } else {
      const payload = v.payload as { verdict?: VerdictInner; specialists?: Record<string, Specialist>; conflict?: { bullish?: string[]; bearish?: string[] }; notes?: string[]; skipped?: Record<string, string>; taking_part?: string[] };
      const inner = payload.verdict ?? {};
      const band = inner.verdict ?? 'no view';
      const head = el('div', 'vhead');
      head.append(el('span', `chip ${BAND[band] ?? 'flat'}`, band));
      head.append(el('span', 'vconv', `conviction ${inner.conviction ?? '—'}${inner.raw_conviction !== undefined ? ` (raw ${inner.raw_conviction})` : ''}`));
      this.content.append(head);
      if (inner.calibration) this.content.append(el('div', 'note', `${inner.calibration}${inner.trust !== undefined ? ` · trust ${inner.trust}` : ''}: conviction is scaled down until the analysts beat a plain baseline in tests.`));
      const age = d.market_day ? daysBetween(v.data_as_of, d.market_day) : 0;
      this.content.append(el('div', 'note', `Made ${v.made_at.slice(0, 16)} from data as of ${v.data_as_of}${age > 0 ? ` (${age} days of newer prices exist)` : ''}.`));
      const list = el('div', 'analysts');
      for (const [name, s] of Object.entries(payload.specialists ?? {})) {
        const row = el('div', 'analyst');
        const top = el('div', 'atop');
        top.append(el('span', 'an', name.replace(/_/g, ' ')), el('span', `chip ${SIGNAL[s.signal ?? 'neutral'] ?? 'flat'}`, s.signal ?? '—'), el('span', 'ac', `${s.confidence ?? 0}% · ${s.data_coverage ?? ''}`));
        const why = el('div', 'why', s.reasoning ?? '');
        why.hidden = true;
        top.addEventListener('click', () => (why.hidden = !why.hidden));
        row.append(top, why);
        list.append(row);
      }
      this.content.append(list);
      for (const r of inner.key_risks ?? []) this.content.append(el('div', 'flagline', `⚠ ${r}`));
      for (const [name, why] of Object.entries(payload.skipped ?? {})) this.content.append(el('div', 'note', `${name.replace(/_/g, ' ')} not run: ${why}`));
    }
    const make = el('button', 'btn', v ? 'Make a fresh verdict' : 'Make a verdict');
    make.title = 'Runs the analysts on the stored data. About 30 seconds; it calls the language model.';
    make.disabled = this.busy !== '';
    make.addEventListener('click', () => void this.make(d.symbol));
    this.content.append(make);
    if (this.busy) this.content.append(el('div', 'note', `Making a verdict for ${this.busy}… (about 30 seconds)`));
  }

  private async make(symbol: string): Promise<void> {
    const r = await fetch(`/api/verdict/${encodeURIComponent(symbol)}`, { method: 'POST' });
    if (r.status === 409) return void this.content.append(el('div', 'flagline', 'Another verdict is already being made; try again when it finishes.'));
    if (!r.ok) return void this.content.append(el('div', 'flagline', 'The verdict could not be started.'));
    this.busy = symbol;
    void this.refresh();
    this.timer = setInterval(async () => {
      const s = (await (await fetch('/api/verdict-job')).json()) as { state: string; message?: string };
      if (s.state === 'running') return;
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      this.busy = '';
      await this.refresh();
      if (s.state === 'failed') this.content.append(el('div', 'flagline', s.message ?? 'The verdict failed.'));
    }, 2000);
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    super.dispose();
  }
}
