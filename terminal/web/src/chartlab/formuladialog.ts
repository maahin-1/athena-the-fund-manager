import { el } from '../panels/base';
import { compile } from '../formula/parse';
import type { SavedFormula } from '../formula/defs';

const EXAMPLES: [string, string][] = [
  ['sma(20) - sma(50)', 'the gap between two averages'],
  ['(high + low) / 2', 'the middle of each bar'],
  ['close / highest(close, 252) - 1', 'distance below the 52-week high'],
  ['rsi(14) > 70', 'yes (1) or no (0): overbought'],
  ['ema(12) crosses above ema(26)', 'yes on the bar of the crossing'],
  ['bbands(20, 2).upper - bbands(20, 2).lower', 'the width of the Bollinger bands'],
];

/** The editor of one formula indicator: a name, where it is drawn, and the formula, with the compiler's verdict shown as it is typed. `save` returns an error
 *  text to keep the dialog open, or null when the formula was kept. The name of a formula that already exists is fixed. */
export function formulaDialog(parent: HTMLElement, initial: SavedFormula | null, save: (formula: SavedFormula) => Promise<string | null>): HTMLElement {
  const overlay = el('div', 'lab-modal');
  const box = el('div', 'lab-dialog lab-formula');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', 'formula indicator');
  box.append(el('h3', '', initial ? `Formula: ${initial.name}` : 'New formula indicator'));

  const name = el('input', 'cmd');
  name.setAttribute('aria-label', 'formula name');
  name.placeholder = 'name, for example Spread';
  name.maxLength = 40;
  name.value = initial?.name ?? '';
  name.readOnly = initial !== null;

  const pane = el('select', 'picker');
  pane.setAttribute('aria-label', 'formula pane');
  pane.append(new Option('In a pane of its own', 'own'), new Option('On the price chart', 'overlay'));
  pane.value = initial?.pane ?? 'own';

  const text = el('textarea', 'lab-formula-text');
  text.setAttribute('aria-label', 'formula text');
  text.rows = 3;
  text.maxLength = 500;
  text.spellcheck = false;
  text.placeholder = 'for example  sma(20) - sma(50)';
  text.value = initial?.text ?? '';

  const verdict = el('div', 'note');
  verdict.setAttribute('role', 'status');
  const check = (): boolean => {
    if (!text.value.trim()) {
      verdict.textContent = '';
      verdict.className = 'note';
      return false;
    }
    const r = compile(text.value);
    if (r.ok) {
      verdict.textContent = r.value.kind === 'expr' ? 'Draws as a line.' : 'A yes-or-no formula: draws as 1 for yes and 0 for no.';
      verdict.className = 'note ok';
      return true;
    }
    verdict.textContent = `${r.error.message} (at character ${r.error.at + 1})`;
    verdict.className = 'note bad';
    return false;
  };
  text.addEventListener('input', check);

  const help = el('details', 'lab-formula-help');
  help.append(el('summary', '', 'What can I write?'));
  help.append(
    el('div', 'note', 'Prices: open high low close volume. Indicators: sma ema wma bbands sar channel rsi macd stoch adx atr obv cci mfi willr roc, with their settings in brackets and a line after a dot (bbands(20, 2).upper). highest(x, n), lowest(x, n), average(x, n) over the last n bars; x[1] is the value one bar ago. + - * /, > >= < <=, crosses above, crosses below, and, or, not.'),
  );
  for (const [formula, what] of EXAMPLES) {
    const row = el('div', 'lab-example');
    const use = el('button', 'linklike', formula);
    use.type = 'button';
    use.addEventListener('click', () => {
      text.value = formula;
      check();
      text.focus();
    });
    row.append(use, el('span', 'note', ` ${what}`));
    help.append(row);
  }

  const message = el('div', 'note bad');
  const ok = el('button', 'btn', 'Save');
  ok.type = 'button';
  ok.addEventListener('click', async () => {
    if (!check() && text.value.trim()) return void (message.textContent = 'Fix the formula first.');
    message.textContent = '';
    const error = await save({ name: name.value.trim(), text: text.value.trim(), pane: pane.value as 'own' | 'overlay' });
    if (error) message.textContent = error;
    else overlay.remove();
  });
  const cancel = el('button', 'btn', 'Cancel');
  cancel.type = 'button';
  cancel.addEventListener('click', () => overlay.remove());
  overlay.addEventListener('keydown', (e) => e.key === 'Escape' && overlay.remove());

  const row = (label: string, field: HTMLElement): HTMLElement => {
    const r = el('label', 'lab-row');
    r.append(el('span', '', label), field);
    return r;
  };
  const form = el('div', 'lab-form');
  form.append(row('Name', name), row('Drawn', pane), row('Formula', text));
  const buttons = el('div', 'lab-buttons');
  buttons.append(ok, cancel);
  box.append(form, verdict, help, message, buttons);
  overlay.append(box);
  parent.append(overlay);
  check();
  (initial ? text : name).focus();
  return overlay;
}
