// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { StrategyBuilder } from '../src/strategybuilder';
import type { StrategiesIO } from '../src/formula/strategies';

function make(initial: unknown | null = null) {
  const saved: string[] = [];
  const io: StrategiesIO = { async load() { return initial; }, async save(body) { saved.push(body); } };
  const run = vi.fn();
  const b = new StrategyBuilder(io, run);
  document.body.replaceChildren(b.element);
  b.element.open = true;
  const q = (label: string) => b.element.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
  const type = (label: string, value: string) => {
    const f = q(label);
    f.value = value;
    f.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const click = (text: string) => ([...b.element.querySelectorAll('button')].find((x) => x.textContent?.startsWith(text)) as HTMLButtonElement).click();
  return { b, run, saved, q, type, click };
}

describe('the strategy builder', () => {
  it('starts from a built-in strategy: its name, its formulas and its stop fill the form', () => {
    const { q, click } = make();
    click('Golden cross');
    expect(q('strategy name').value).toBe('Golden cross');
    expect(q('entry formula').value).toBe('sma(50) crosses above sma(200)');
    expect(q('exit formula').value).toBe('sma(50) crosses below sma(200)');
    click('RSI mean reversion');
    expect(q('protective stop').value).toBe('2');
  });

  it('shows what each formula is as it is typed, and where a mistake is', () => {
    const { b, type } = make();
    type('entry formula', 'close > smaa(50)');
    expect(b.element.querySelector('.sb-entry-verdict')?.textContent).toContain('unknown function "smaa"; did you mean "sma"?');
    expect(b.element.querySelector('.sb-entry-verdict')?.textContent).toContain('character 9');
    type('entry formula', 'sma(50)');
    expect(b.element.querySelector('.sb-entry-verdict')?.textContent).toContain('number');
    type('entry formula', 'close > sma(50)');
    expect(b.element.querySelector('.sb-entry-verdict')?.textContent).toContain('yes/no');
  });

  it('runs the tree the form describes, for the symbol it was given', () => {
    const { b, run, q, type, click } = make();
    b.setSymbol('TCS');
    expect([...b.element.querySelectorAll('button')].some((x) => x.textContent === 'Run on TCS')).toBe(true);
    type('strategy name', 'Mine');
    type('entry formula', 'close > sma(50)');
    type('exit formula', 'close < sma(50)');
    type('protective stop', '2');
    click('Run on');
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][0]).toMatchObject({ version: 1, name: 'Mine', stop_atr: 2, entry: { op: 'gt' }, exit: { op: 'lt' } });
    expect(q('strategy name').value).toBe('Mine');
  });

  it('does not run a form that is wrong, and says which field and why', () => {
    const { b, run, type, click } = make();
    b.setSymbol('SBIN');
    type('strategy name', 'Mine');
    type('entry formula', 'sma(50)');
    type('exit formula', 'close < 1');
    click('Run on');
    expect(run).not.toHaveBeenCalled();
    expect(b.element.querySelector('.sb-message')?.textContent).toMatch(/entry must be a yes\/no condition/);
    type('entry formula', 'close > 1');
    type('protective stop', '99');
    click('Run on');
    expect(b.element.querySelector('.sb-message')?.textContent).toMatch(/protective stop must be above 0 and at most 10/);
    expect(run).not.toHaveBeenCalled();
  });

  it('saves a strategy under its name, lists it, opens it again and deletes it', async () => {
    const { b, saved, q, type, click } = make();
    type('strategy name', 'Keep me');
    type('entry formula', 'close > 1');
    type('exit formula', 'close < 1');
    click('Save');
    await vi.waitFor(() => expect(saved).toHaveLength(1));
    expect(JSON.parse(saved[0]).strategies[0]).toMatchObject({ name: 'Keep me', form: { entry: 'close > 1' } });
    await vi.waitFor(() => expect([...q('saved strategies').querySelectorAll('option')].map((o) => o.textContent)).toContain('Keep me'));
    type('entry formula', 'changed');
    q('saved strategies').value = 'Keep me';
    q('saved strategies').dispatchEvent(new Event('change'));
    expect(q('entry formula').value).toBe('close > 1');
    click('Delete');
    await vi.waitFor(() => expect(saved).toHaveLength(2));
    expect(JSON.parse(saved[1]).strategies).toEqual([]);
    await vi.waitFor(() => expect(b.element.querySelector('.sb-message')?.textContent).toContain('Deleted'));
  });

  it('opens with the strategies the server has', async () => {
    const { q } = make({ version: 1, strategies: [{ name: 'Server one', form: { entry: 'close > 1', exit: 'close < 1' } }] });
    await vi.waitFor(() => expect([...q('saved strategies').querySelectorAll('option')].map((o) => o.textContent)).toContain('Server one'));
  });
});
