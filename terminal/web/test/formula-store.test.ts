import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { find, setCustom, customDefs } from '../src/chartlab/registry';
import { deserialize, serialize } from '../src/chartlab/store';
import { ChartModel } from '../src/chartlab/model';
import { defFor, formulaId } from '../src/formula/defs';
import { FormulaStore, FormulasError, MAX_FORMULAS, MAX_NAME, MAX_TEXT, validateFormulas, type FormulaIO, type FormulasDoc } from '../src/formula/store';

const bars = (closes: number[]): Bar[] => closes.map((c, i) => ({ t: i, o: c - 1, h: c + 2, l: c - 3, c, v: 100 }));
const f = (name: string, text = 'sma(2)', pane: 'overlay' | 'own' = 'own') => ({ name, text, pane });
const doc = (...names: string[]): FormulasDoc => ({ version: 1, formulas: names.map((n) => f(n)) });

describe('a formula as an indicator', () => {
  it('draws a number-valued formula as one line, empty where it cannot be computed', () => {
    const made = defFor(f('Spread', 'sma(2) - close'));
    expect('def' in made).toBe(true);
    if (!('def' in made)) return;
    expect(made.def.id).toBe('f:Spread');
    expect(made.def.pane).toBe('own');
    expect(made.def.inputs).toEqual([]);
    expect(made.def.outputs).toHaveLength(1);
    expect(made.def.compute(bars([1, 2, 3, 5]), {}).value).toEqual([null, -0.5, -0.5, -1]);
  });

  it('draws a yes-or-no formula as 1 for yes and 0 for no, empty where it cannot be decided', () => {
    const made = defFor(f('Up', 'close > close[1]'));
    if (!('def' in made)) throw new Error('should compile');
    expect(made.def.compute(bars([1, 2, 1, 3]), {}).value).toEqual([null, 1, 0, 1]);
  });

  it('can sit on the price chart', () => {
    const made = defFor(f('Mid', '(high + low) / 2', 'overlay'));
    if (!('def' in made)) throw new Error('should compile');
    expect(made.def.pane).toBe('overlay');
  });

  it('says what is wrong with a formula that does not compile', () => {
    const bad = defFor(f('Bad', 'smaa(20)'));
    expect('error' in bad && bad.error.message).toMatch(/unknown function "smaa"/);
  });
});

describe('custom indicators in the registry', () => {
  it('are found by id beside the built-in ones, and replaced as a set', () => {
    const made = defFor(f('Mine', 'close'));
    if (!('def' in made)) throw new Error('should compile');
    setCustom([made.def]);
    expect(find('f:Mine')?.name).toBe('Mine');
    expect(find('sma')?.name).toBe('Simple Moving Average');
    expect(customDefs().map((d) => d.id)).toEqual(['f:Mine']);
    setCustom([]);
    expect(find('f:Mine')).toBeUndefined();
  });

  it('go on a chart like any indicator and survive saving the chart state', () => {
    const made = defFor(f('Spread', 'close - sma(2)'));
    if (!('def' in made)) throw new Error('should compile');
    setCustom([made.def]);
    const model = new ChartModel();
    model.setBars(bars([1, 2, 3, 4, 5]), '1D');
    const uid = model.addIndicator(formulaId('Spread'))!;
    expect(uid).toBeTruthy();
    expect(model.series(uid).value).toEqual([null, 0.5, 0.5, 0.5, 0.5]);
    const kept = deserialize(serialize(model.state()));
    expect(kept.indicators.map((i) => i.id)).toEqual(['f:Spread']);
    setCustom([]);
  });

  it('are taken off a chart when the formula is deleted, and recomputed when it is edited', () => {
    const first = defFor(f('Line', 'close'));
    if (!('def' in first)) throw new Error('should compile');
    setCustom([first.def]);
    const model = new ChartModel();
    model.setBars(bars([1, 2, 3]), '1D');
    const uid = model.addIndicator('f:Line')!;
    model.addIndicator('rsi');
    expect(model.series(uid).value).toEqual([1, 2, 3]);
    const edited = defFor(f('Line', 'close * 2'));
    if (!('def' in edited)) throw new Error('should compile');
    setCustom([edited.def]);
    expect(model.refreshCustom()).toBe(false); // nothing lost, but the old numbers are forgotten
    expect(model.series(uid).value).toEqual([2, 4, 6]);
    setCustom([]);
    expect(model.refreshCustom()).toBe(true);
    expect(model.indicators.map((i) => i.id)).toEqual(['rsi']);
  });

  it('a saved chart that names a formula that no longer exists keeps the rest rather than failing whole', () => {
    const text = JSON.stringify({
      version: 1, type: 'candles', scale: 'auto', adjusted: true, compare: null,
      indicators: [{ id: 'rsi', params: { length: 14 }, hidden: false, colors: {} }, { id: 'f:Gone', params: {}, hidden: false, colors: {} }],
    });
    expect(deserialize(text).indicators.map((i) => i.id)).toEqual(['rsi']);
    expect(() => deserialize(text.replace('f:Gone', 'nonsense'))).toThrow(/unknown indicator/); // only a formula may go missing
  });
});

describe('the saved formulas document', () => {
  it('accepts what it wrote', () => {
    expect(validateFormulas(doc('A', 'B')).formulas.map((x) => x.name)).toEqual(['A', 'B']);
    expect(validateFormulas({ version: 1, formulas: [{ name: 'Lean', text: 'close' }] }).formulas[0].pane).toBe('own');
  });

  it('refuses what is not a document, a bad name, a long text, a repeated name, a bad pane or too many', () => {
    expect(() => validateFormulas([])).toThrow(FormulasError);
    expect(() => validateFormulas({ version: 2, formulas: [] })).toThrow(/version/);
    expect(() => validateFormulas({ version: 1, formulas: [f('')] })).toThrow(/name/);
    expect(() => validateFormulas({ version: 1, formulas: [f('a/b')] })).toThrow(/name/);
    expect(() => validateFormulas({ version: 1, formulas: [f('x'.repeat(MAX_NAME + 1))] })).toThrow(/name/);
    expect(() => validateFormulas({ version: 1, formulas: [f('A', 'x'.repeat(MAX_TEXT + 1))] })).toThrow(/at most/);
    expect(() => validateFormulas({ version: 1, formulas: [f('A'), f('a')] })).toThrow(/twice/);
    expect(() => validateFormulas({ version: 1, formulas: [{ name: 'A', text: 'close', pane: 'left' }] })).toThrow(/pane/);
    expect(() => validateFormulas({ version: 1, formulas: Array.from({ length: MAX_FORMULAS + 1 }, (_, i) => f(`F${i}`)) })).toThrow(/at most/);
  });

  it('keeps a formula that does not compile, for the editor to show its error', () => {
    expect(validateFormulas({ version: 1, formulas: [f('Broken', 'smaa(')] }).formulas[0].text).toBe('smaa(');
  });
});

describe('the store', () => {
  const memory = (initial: unknown | null): FormulaIO & { saved: string[] } => {
    const io = { saved: [] as string[], async load() { return initial; }, async save(body: string) { io.saved.push(body); } };
    return io;
  };
  const noLocal = null;

  it('registers the formulas that compile, reports the ones that do not, and tells listeners', async () => {
    const io = memory({ version: 1, formulas: [f('Good', 'sma(3)'), f('Bad', 'sma(')] });
    const store = new FormulaStore(io, noLocal);
    let heard = 0;
    store.subscribe(() => heard++);
    await store.load();
    expect(customDefs().map((d) => d.id)).toEqual(['f:Good']);
    expect(Object.keys(store.errors)).toEqual(['Bad']);
    expect(store.errors.Bad).toMatch(/missing "\)"/);
    expect(heard).toBeGreaterThan(0);
    setCustom([]);
  });

  it('saves a new or edited formula to the server and registers it at once, and deletes one', async () => {
    const io = memory(null);
    const store = new FormulaStore(io, noLocal);
    await store.load();
    await store.save(f('One', 'close'));
    expect(find('f:One')).toBeDefined();
    expect(JSON.parse(io.saved[0]).formulas.map((x: { name: string }) => x.name)).toEqual(['One']);
    await store.save(f('one', 'open')); // the same name in another case replaces it
    expect(store.names()).toEqual(['one']);
    await store.remove('ONE');
    expect(find('f:one')).toBeUndefined();
    expect(store.names()).toEqual([]);
  });

  it('refuses to save a formula that does not compile, saying why', async () => {
    const store = new FormulaStore(memory(null), noLocal);
    await expect(store.save(f('Bad', 'close >'))).rejects.toThrow(/expected a value/);
    expect(store.names()).toEqual([]);
  });

  it('keeps a copy in the browser so a chart can be restored before the server answers', async () => {
    const kept = new Map<string, string>();
    const local = { getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => void kept.set(k, v) };
    const first = new FormulaStore(memory(null), local);
    await first.load();
    await first.save(f('Cached', 'close'));
    setCustom([]);
    const second = new FormulaStore({ async load() { throw new Error('offline'); }, async save() {} }, local);
    expect(find('f:Cached')).toBeDefined(); // registered from the browser copy at once
    await second.load();
    expect(second.problem).toMatch(/could not be loaded/);
    expect(find('f:Cached')).toBeDefined();
    setCustom([]);
  });
});
