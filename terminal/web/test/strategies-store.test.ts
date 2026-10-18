import { describe, expect, it } from 'vitest';
import { blankForm, type StrategyForm } from '../src/formula/strategy';
import { MAX_STRATEGIES, SavedStrategies, StrategiesError, deleteStrategy, saveStrategy, validateStrategies, type StrategiesDoc, type StrategiesIO } from '../src/formula/strategies';

const form = (extra: Partial<StrategyForm> = {}): StrategyForm => ({ ...blankForm(), entry: 'close > sma(50)', exit: 'close < sma(50)', ...extra });
const one = (name: string, f: Partial<StrategyForm> = {}) => ({ name, form: form(f) });
const doc = (...names: string[]): StrategiesDoc => ({ version: 1, strategies: names.map((n) => one(n)) });

describe('the saved strategies document', () => {
  it('accepts what it wrote and fills a form that lacks fields', () => {
    expect(validateStrategies(doc('A', 'B')).strategies.map((s) => s.name)).toEqual(['A', 'B']);
    const lean = validateStrategies({ version: 1, strategies: [{ name: 'Lean', form: { entry: 'close > 1', exit: 'close < 1' } }] }).strategies[0];
    expect(lean.form).toEqual({ ...blankForm(), name: 'Lean', entry: 'close > 1', exit: 'close < 1' });
  });

  it('refuses what is not a document, a bad or repeated name, a text that is too long, or too many', () => {
    expect(() => validateStrategies([])).toThrow(StrategiesError);
    expect(() => validateStrategies({ version: 3, strategies: [] })).toThrow(/version/);
    expect(() => validateStrategies({ version: 1, strategies: [one('')] })).toThrow(/name/);
    expect(() => validateStrategies({ version: 1, strategies: [one('x'.repeat(61))] })).toThrow(/name/);
    expect(() => validateStrategies({ version: 1, strategies: [one('A'), one('a')] })).toThrow(/twice/);
    expect(() => validateStrategies({ version: 1, strategies: [one('A', { entry: 'x'.repeat(501) })] })).toThrow(/at most 500/);
    expect(() => validateStrategies({ version: 1, strategies: [{ name: 'A', form: { entry: 5 } }] })).toThrow(/entry/);
    expect(() => validateStrategies({ version: 1, strategies: Array.from({ length: MAX_STRATEGIES + 1 }, (_, i) => one(`S${i}`)) })).toThrow(/at most/);
  });

  it('keeps a strategy whose formulas do not compile, for the form to show why', () => {
    expect(validateStrategies({ version: 1, strategies: [one('Broken', { entry: 'smaa(' })] }).strategies[0].form.entry).toBe('smaa(');
  });
});

describe('saving and deleting', () => {
  it('replace the strategy of that name (any case) in its place, and add a new one last', () => {
    const next = saveStrategy(doc('A', 'B', 'C'), one('b', { exit: 'close < 1' }));
    expect(next.strategies.map((s) => s.name)).toEqual(['A', 'b', 'C']);
    expect(next.strategies[1].form.exit).toBe('close < 1');
    expect(saveStrategy(doc('A'), one('Z')).strategies.map((s) => s.name)).toEqual(['A', 'Z']);
    expect(() => saveStrategy(doc(...Array.from({ length: MAX_STRATEGIES }, (_, i) => `S${i}`)), one('One more'))).toThrow(/at most/);
    expect(deleteStrategy(doc('A', 'B'), 'a').strategies.map((s) => s.name)).toEqual(['B']);
  });
});

describe('the store', () => {
  const memory = (initial: unknown | null): StrategiesIO & { saved: string[] } => {
    const io = { saved: [] as string[], async load() { return initial; }, async save(body: string) { io.saved.push(body); } };
    return io;
  };

  it('loads, saves each change and starts empty with a message when the server is down or the document is damaged', async () => {
    const io = memory(doc('A'));
    const store = new SavedStrategies(io);
    await store.load();
    expect(store.names()).toEqual(['A']);
    await store.save(one('B'));
    expect(JSON.parse(io.saved[0]).strategies.map((s: { name: string }) => s.name)).toEqual(['A', 'B']);
    await store.remove('A');
    expect(store.names()).toEqual(['B']);
    expect(store.get('b')?.form.entry).toBe('close > sma(50)');
    const down = new SavedStrategies({ async load() { throw new Error('offline'); }, async save() { throw new Error('offline'); } });
    await down.load();
    expect(down.problem).toMatch(/could not be loaded/);
    await expect(down.save(one('A'))).rejects.toThrow();
    expect(down.names()).toEqual(['A']); // kept for the session
    const damaged = new SavedStrategies(memory({ version: 9 }));
    await damaged.load();
    expect(damaged.problem).toMatch(/could not be read/);
  });
});
