import { describe, expect, it } from 'vitest';
import goldenFile from './fixtures/formula-golden.json';
import { PRESET_FORMS, blankForm, buildStrategy, type StrategyForm } from '../src/formula/strategy';

const golden = goldenFile as unknown as { presets: { key: string; entry: string; exit: string; tree: unknown }[] };
const form = (extra: Partial<StrategyForm> = {}): StrategyForm => ({ ...blankForm(), name: 'Mine', entry: 'close > sma(50)', exit: 'close < sma(50)', ...extra });
const built = (f: StrategyForm) => {
  const r = buildStrategy(f);
  if (!r.ok) throw new Error(`${r.field}: ${r.message}`);
  return r.tree;
};
const failed = (f: StrategyForm) => {
  const r = buildStrategy(f);
  if (r.ok) throw new Error('should not build');
  return r;
};

describe('the strategy the form describes', () => {
  it('is the version 1 tree: a name, an entry and an exit', () => {
    expect(built(form())).toEqual({
      version: 1, name: 'Mine',
      entry: { op: 'gt', left: { price: 'close' }, right: { ind: 'sma', params: { length: 50 }, line: 'value' } },
      exit: { op: 'lt', left: { price: 'close' }, right: { ind: 'sma', params: { length: 50 }, line: 'value' } },
    });
  });

  it('adds the risk options that are filled in, as numbers, and leaves the others out', () => {
    expect(built(form({ stop_atr: '2', take_profit_pct: '25.5', max_hold_bars: '60', position_pct: '50', trailing_stop_atr: '3' }))).toMatchObject({
      stop_atr: 2, trailing_stop_atr: 3, take_profit_pct: 25.5, max_hold_bars: 60, position_pct: 50,
    });
    const plain = built(form({ stop_atr: '  ' })) as unknown as Record<string, unknown>;
    expect('stop_atr' in plain).toBe(false);
    expect('position_pct' in plain).toBe(false);
  });
});

describe('what is refused, and where', () => {
  it('names the field and the place in its formula', () => {
    const e = failed(form({ entry: 'close > smaa(50)' }));
    expect(e.field).toBe('entry');
    expect(e.message).toMatch(/unknown function "smaa"; did you mean "sma"\?/);
    expect(e.at).toBe(8);
    expect(failed(form({ exit: 'rsi(14) <' })).field).toBe('exit');
  });

  it('wants a condition, not a number, for the entry and the exit', () => {
    expect(failed(form({ entry: 'sma(50)' })).message).toMatch(/entry must be a yes\/no condition/);
    expect(failed(form({ exit: 'close - open' })).message).toMatch(/exit must be a yes\/no condition/);
    expect(failed(form({ entry: '' })).message).toMatch(/entry needs a formula/);
  });

  it('wants a name of 1 to 60 characters', () => {
    expect(failed(form({ name: '  ' })).field).toBe('name');
    expect(failed(form({ name: 'x'.repeat(61) })).field).toBe('name');
  });

  it('keeps each risk option inside the engine\'s limits', () => {
    expect(failed(form({ stop_atr: '0' })).message).toMatch(/stop must be above 0 and at most 10/);
    expect(failed(form({ stop_atr: '11' })).field).toBe('stop_atr');
    expect(failed(form({ trailing_stop_atr: 'x' })).field).toBe('trailing_stop_atr');
    expect(failed(form({ take_profit_pct: '1001' })).field).toBe('take_profit_pct');
    expect(failed(form({ max_hold_bars: '2.5' })).message).toMatch(/whole number/);
    expect(failed(form({ max_hold_bars: '1001' })).field).toBe('max_hold_bars');
    expect(failed(form({ position_pct: '101' })).field).toBe('position_pct');
    expect(built(form({ position_pct: '100', stop_atr: '10', max_hold_bars: '1000', take_profit_pct: '1000' }))).toMatchObject({ stop_atr: 10 });
  });
});

describe('the built-in strategies as formulas', () => {
  it('are the starting points, one for each preset the engine holds', () => {
    expect(PRESET_FORMS.map((p) => p.key).sort()).toEqual(golden.presets.map((p) => p.key).sort());
  });

  it('build to exactly the trees the Python presets hold', () => {
    for (const preset of golden.presets) {
      const start = PRESET_FORMS.find((p) => p.key === preset.key)!;
      expect(start.form.entry, preset.key).toBe(preset.entry);
      expect(start.form.exit, preset.key).toBe(preset.exit);
      expect(built(start.form), preset.key).toEqual(preset.tree);
    }
  });
});
