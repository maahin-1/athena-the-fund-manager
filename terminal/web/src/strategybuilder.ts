import { compile } from './formula/parse';
import { SavedStrategies, StrategiesError, type StrategiesIO } from './formula/strategies';
import { PRESET_FORMS, blankForm, buildStrategy, type StrategyField, type StrategyForm, type StrategyTree } from './formula/strategy';
import { el } from './panels/base';

const RISK_LABELS: [Exclude<StrategyField, 'name' | 'entry' | 'exit'>, string, string][] = [
  ['stop_atr', 'protective stop', 'Sell when the close falls this many average true ranges below the entry price (0 to 10). Empty: no stop.'],
  ['trailing_stop_atr', 'trailing stop', 'Sell when the close falls this many average true ranges below the highest close since entry. Empty: none.'],
  ['take_profit_pct', 'profit target', 'Sell when the gain reaches this percent (up to 1000). Empty: none.'],
  ['max_hold_bars', 'longest hold', 'Sell after this many bars whatever else happens (a whole number, up to 1000). Empty: none.'],
  ['position_pct', 'cash per entry', 'The share of cash put into each entry, in percent (up to 100). Empty: all of it.'],
];

/** The strategy builder: write an entry and an exit as formulas, choose the risk options, and run the strategy against buy and hold on the stock on show. The
 *  form builds the tree the backtest engine reads (`formula/strategy.ts`); the engine validates it again. Saved strategies are kept on the server. */
export class StrategyBuilder {
  readonly element = el('details', 'sbuilder');
  private readonly saved: SavedStrategies;
  private readonly name = el('input', 'cmd');
  private readonly entry = el('textarea', 'lab-formula-text');
  private readonly exit = el('textarea', 'lab-formula-text');
  private readonly risk = new Map<string, HTMLInputElement>();
  private readonly entryVerdict = el('div', 'note sb-entry-verdict');
  private readonly exitVerdict = el('div', 'note sb-exit-verdict');
  private readonly message = el('div', 'note sb-message');
  private readonly pick = el('select', 'picker');
  private readonly runButton = el('button', 'btn', 'Run');

  constructor(io: StrategiesIO, private onRun: (tree: StrategyTree) => void) {
    this.saved = new SavedStrategies(io);
    this.element.append(el('summary', '', 'Test your own strategy'));
    this.element.append(el('div', 'note', 'Buy when the entry is true, sell when the exit is true, long only. The formulas are the ones of the Chart Lab (close > sma(200), rsi(14) < 30, ema(12) crosses above ema(26)). The result is replayed day by day against buy and hold.'));

    const starts = el('div', 'sb-row');
    starts.append(el('span', 'note', 'Start from: '));
    for (const p of PRESET_FORMS) {
      const b = el('button', 'btn', p.form.name);
      b.type = 'button';
      b.addEventListener('click', () => this.fill(p.form));
      starts.append(b);
    }

    const savedRow = el('div', 'sb-row');
    this.pick.setAttribute('aria-label', 'saved strategies');
    this.pick.addEventListener('change', () => {
      const s = this.saved.get(this.pick.value);
      if (s) this.fill(s.form);
    });
    const save = el('button', 'btn', 'Save');
    save.type = 'button';
    save.addEventListener('click', () => void this.save());
    const del = el('button', 'btn', 'Delete');
    del.type = 'button';
    del.addEventListener('click', () => void this.remove());
    savedRow.append(this.pick, save, del);

    this.name.placeholder = 'name this strategy';
    this.name.setAttribute('aria-label', 'strategy name');
    this.name.maxLength = 60;
    for (const [box, label, verdict, side] of [[this.entry, 'entry formula', this.entryVerdict, 'entry'], [this.exit, 'exit formula', this.exitVerdict, 'exit']] as const) {
      box.setAttribute('aria-label', label);
      box.rows = 2;
      box.maxLength = 500;
      box.spellcheck = false;
      box.addEventListener('input', () => this.verdict(side, box.value, verdict));
    }
    this.entry.placeholder = 'buy when…  for example  sma(50) crosses above sma(200)';
    this.exit.placeholder = 'sell when…  for example  sma(50) crosses below sma(200)';

    const grid = el('div', 'sb-risk');
    for (const [field, label, hint] of RISK_LABELS) {
      const input = el('input', 'snum');
      input.setAttribute('aria-label', label);
      input.title = hint;
      input.inputMode = 'decimal';
      input.placeholder = 'none';
      this.risk.set(field, input);
      const wrap = el('label', 'sb-risk-item');
      wrap.append(el('span', 'k', label), input);
      grid.append(wrap);
    }

    this.runButton.type = 'button';
    this.runButton.addEventListener('click', () => this.run());
    const actions = el('div', 'sb-row');
    actions.append(this.runButton);
    this.element.append(starts, savedRow, this.name, this.entry, this.entryVerdict, this.exit, this.exitVerdict, grid, actions, this.message);
    this.setSymbol('');
    void this.saved.load().then(() => this.refresh());
  }

  /** The stock the Run button will test. */
  setSymbol(symbol: string): void {
    this.runButton.textContent = symbol ? `Run on ${symbol}` : 'Run';
    this.runButton.disabled = !symbol;
  }

  private form(): StrategyForm {
    const f = blankForm();
    f.name = this.name.value;
    f.entry = this.entry.value;
    f.exit = this.exit.value;
    for (const [field, input] of this.risk) f[field as 'stop_atr'] = input.value;
    return f;
  }

  private fill(f: StrategyForm): void {
    this.name.value = f.name;
    this.entry.value = f.entry;
    this.exit.value = f.exit;
    for (const [field, input] of this.risk) input.value = f[field as 'stop_atr'];
    this.verdict('entry', f.entry, this.entryVerdict);
    this.verdict('exit', f.exit, this.exitVerdict);
    this.message.textContent = '';
  }

  private verdict(side: 'entry' | 'exit', text: string, out: HTMLElement): void {
    if (!text.trim()) {
      out.textContent = '';
      out.className = `note sb-${side}-verdict`;
      return;
    }
    const r = compile(text);
    const ok = r.ok && r.value.kind === 'cond';
    out.className = `note sb-${side}-verdict ${ok ? 'ok' : 'bad'}`;
    out.textContent = !r.ok ? `${r.error.message} (at character ${r.error.at + 1})` : r.value.kind === 'cond' ? 'A yes/no condition.' : `This formula is a number, not a yes/no condition: compare it with something, for example ${text.trim()} > 0.`;
  }

  private run(): void {
    const built = buildStrategy(this.form());
    if (!built.ok) {
      this.message.className = 'note bad sb-message';
      this.message.textContent = built.message.startsWith('the ') ? built.message[0].toUpperCase() + built.message.slice(1) : `${built.field}: ${built.message}`;
      return;
    }
    this.message.className = 'note sb-message';
    this.message.textContent = '';
    this.onRun(built.tree);
  }

  private refresh(): void {
    this.pick.replaceChildren(new Option(`Saved strategies (${this.saved.names().length})`, ''), ...this.saved.names().map((n) => new Option(n, n)));
    if (this.saved.problem) this.say(this.saved.problem, true);
  }

  private say(text: string, bad = false): void {
    this.message.className = `note sb-message${bad ? ' bad' : ''}`;
    this.message.textContent = text;
  }

  private async save(): Promise<void> {
    const f = this.form();
    const name = f.name.trim();
    if (!name) return this.say('Give the strategy a name first.', true);
    try {
      await this.saved.save({ name, form: f });
      this.refresh();
      this.pick.value = name;
      this.say(`Saved "${name}".`);
    } catch (e) {
      this.refresh();
      this.say(e instanceof StrategiesError ? e.message : 'Could not save: the server did not answer. The strategy is kept until you reload.', true);
    }
  }

  private async remove(): Promise<void> {
    const name = this.pick.value || this.name.value.trim();
    if (!name || !this.saved.get(name)) return this.say('Pick a saved strategy to delete.', true);
    try {
      await this.saved.remove(name);
      this.refresh();
      this.say(`Deleted "${name}".`);
    } catch {
      this.refresh();
      this.say('Could not delete: the server did not answer.', true);
    }
  }
}
