import { describe, expect, it } from 'vitest';
import vectors from './fixtures/indicator_vectors.json';
import type { Bar } from '../src/bars';
import { caption, defaults, find, REGISTRY, validate } from '../src/chartlab/registry';

const bars = vectors.bars as Bar[];

describe('indicator registry', () => {
  it('has unique ids and the promised overlay and own-pane indicators', () => {
    const ids = REGISTRY.map((d) => d.id);
    expect(new Set(ids).size).toBe(ids.length);
    const overlay = REGISTRY.filter((d) => d.pane === 'overlay').map((d) => d.id).sort();
    expect(overlay).toEqual(['bb', 'dema', 'donchian', 'ema', 'hma', 'kama', 'keltner', 'linreg', 'sar', 'sma', 'supertrend', 'tema', 'trima', 'vwap', 'wma']);
    const own = REGISTRY.filter((d) => d.pane === 'own').map((d) => d.id).sort();
    expect(own).toEqual(['ad', 'adosc', 'adx', 'aroon', 'aroonosc', 'atr', 'ao', 'bbpb', 'bbw', 'bop', 'cci', 'chop', 'cmf', 'cmo', 'di', 'force', 'hv', 'macd', 'mfi', 'mom', 'natr', 'obv', 'ppo', 'roc', 'rsi', 'stddev', 'stoch', 'stochrsi', 'trix', 'ultosc', 'vortex', 'willr'].sort());
  });

  it('computes every declared output at the length of the bars with its defaults', () => {
    for (const def of REGISTRY) {
      const out = def.compute(bars, defaults(def));
      for (const o of def.outputs) {
        expect(out[o.key], `${def.id}.${o.key}`).toBeDefined();
        expect(out[o.key].length, `${def.id}.${o.key} length`).toBe(bars.length);
        expect(out[o.key].some((v) => v !== null), `${def.id}.${o.key} has values`).toBe(true);
      }
    }
  });

  it('gives only nulls when there are fewer bars than the warm-up', () => {
    const few = bars.slice(0, 5);
    for (const def of REGISTRY.filter((d) => ['sma', 'rsi', 'adx', 'macd'].includes(d.id))) {
      const out = def.compute(few, defaults(def));
      for (const o of def.outputs) expect(out[o.key].every((v) => v === null), `${def.id}.${o.key}`).toBe(true);
    }
  });

  it('completes and limits settings, and refuses a non-number', () => {
    const rsi = find('rsi')!;
    expect(validate(rsi, {})).toEqual({ ok: true, params: { length: 14 } });
    expect(validate(rsi, { length: 9.6 })).toEqual({ ok: true, params: { length: 10 } });
    expect(validate(rsi, { length: 0 })).toEqual({ ok: true, params: { length: 1 } });
    expect(validate(rsi, { length: 100000 })).toEqual({ ok: true, params: { length: 500 } });
    expect(validate(rsi, { length: 'abc' }).ok).toBe(false);
    const bb = find('bb')!;
    expect(validate(bb, { mult: 99 })).toEqual({ ok: true, params: { length: 20, mult: 10 } });
  });

  it('writes a legend caption from the settings', () => {
    expect(caption(find('rsi')!, { length: 14 })).toBe('RSI 14');
    expect(caption(find('macd')!, { fast: 12, slow: 26, signal: 9 })).toBe('MACD 12 26 9');
    expect(caption(find('bb')!, { length: 20, mult: 2 })).toBe('BB 20 2');
    expect(caption(find('ema')!, { length: 20 })).toBe('EMA 20');
    expect(caption(find('vwap')!, {})).toBe('VWAP');
  });
});
