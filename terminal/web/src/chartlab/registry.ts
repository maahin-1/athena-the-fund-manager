import type { Bar } from '../bars';
import * as m from './maths';

/** One setting of an indicator, as the settings dialog shows it. */
export interface Input {
  key: string;
  label: string;
  kind: 'int' | 'float' | 'select';
  default: number | string;
  min?: number;
  max?: number;
  options?: string[];
}

/** One thing an indicator draws: a line or a histogram, coloured by a CSS variable of the page's theme. */
export interface Output {
  key: string;
  label: string;
  color: string;
  kind: 'line' | 'histogram';
  width?: 1 | 2 | 3;
}

export type Params = Record<string, number | string>;

export interface IndicatorDef {
  id: string;
  name: string;
  /** `overlay` draws on the price pane, `own` gets a pane below. */
  pane: 'overlay' | 'own';
  inputs: Input[];
  outputs: Output[];
  /** Horizontal guide lines in an own pane (RSI's 30 and 70). */
  levels?: number[];
  compute(bars: Bar[], params: Params): Record<string, m.Series>;
}

const int = (key: string, label: string, def: number, min = 1, max = 500): Input => ({ key, label, kind: 'int', default: def, min, max });
const flt = (key: string, label: string, def: number, min: number, max: number): Input => ({ key, label, kind: 'float', default: def, min, max });
const line = (key: string, label: string, color: string, width: 1 | 2 | 3 = 1): Output => ({ key, label, color, kind: 'line', width });

const closes = (b: Bar[]) => b.map((x) => x.c);
const highs = (b: Bar[]) => b.map((x) => x.h);
const lows = (b: Bar[]) => b.map((x) => x.l);
const volumes = (b: Bar[]) => b.map((x) => x.v);
const n = (p: Params, key: string) => Number(p[key]);

export const REGISTRY: IndicatorDef[] = [
  { id: 'sma', name: 'Simple Moving Average', pane: 'overlay', inputs: [int('length', 'Length', 50)], outputs: [line('ma', 'SMA', '--ind2', 2)], compute: (b, p) => ({ ma: m.sma(closes(b), n(p, 'length')) }) },
  { id: 'ema', name: 'Exponential Moving Average', pane: 'overlay', inputs: [int('length', 'Length', 20)], outputs: [line('ma', 'EMA', '--ind1', 2)], compute: (b, p) => ({ ma: m.ema(closes(b), n(p, 'length')) }) },
  { id: 'wma', name: 'Weighted Moving Average', pane: 'overlay', inputs: [int('length', 'Length', 20)], outputs: [line('ma', 'WMA', '--ind3', 2)], compute: (b, p) => ({ ma: m.wma(closes(b), n(p, 'length')) }) },
  {
    id: 'bb', name: 'Bollinger Bands', pane: 'overlay', inputs: [int('length', 'Length', 20), flt('mult', 'Std dev', 2, 0.1, 10)],
    outputs: [line('upper', 'Upper', '--ind3'), line('mid', 'Basis', '--ind2'), line('lower', 'Lower', '--ind3')],
    compute: (b, p) => {
      const r = m.bollinger(closes(b), n(p, 'length'), n(p, 'mult'));
      return { upper: r.upper, mid: r.mid, lower: r.lower };
    },
  },
  { id: 'vwap', name: 'VWAP', pane: 'overlay', inputs: [], outputs: [line('vwap', 'VWAP', '--ind2', 2)], compute: (b) => ({ vwap: m.vwap(b.map((x) => x.t), highs(b), lows(b), closes(b), volumes(b)) }) },
  {
    id: 'supertrend', name: 'Supertrend', pane: 'overlay', inputs: [int('length', 'ATR length', 10), flt('mult', 'Factor', 3, 0.1, 20)],
    outputs: [line('line', 'Supertrend', '--ind1', 2)],
    compute: (b, p) => ({ line: m.supertrend(highs(b), lows(b), closes(b), n(p, 'length'), n(p, 'mult')).line }),
  },
  {
    id: 'donchian', name: 'Donchian Channels', pane: 'overlay', inputs: [int('length', 'Length', 20)],
    outputs: [line('upper', 'Upper', '--ind1'), line('mid', 'Basis', '--ind2'), line('lower', 'Lower', '--ind1')],
    compute: (b, p) => ({ ...m.donchian(highs(b), lows(b), n(p, 'length')) }),
  },
  {
    id: 'keltner', name: 'Keltner Channels', pane: 'overlay', inputs: [int('length', 'Length', 20), int('atr', 'ATR length', 10), flt('mult', 'Multiplier', 2, 0.1, 20)],
    outputs: [line('upper', 'Upper', '--ind3'), line('mid', 'Basis', '--ind2'), line('lower', 'Lower', '--ind3')],
    compute: (b, p) => ({ ...m.keltner(highs(b), lows(b), closes(b), n(p, 'length'), n(p, 'atr'), n(p, 'mult')) }),
  },
  {
    id: 'sar', name: 'Parabolic SAR', pane: 'overlay', inputs: [flt('step', 'Start', 0.02, 0.001, 1), flt('max', 'Maximum', 0.2, 0.01, 1)],
    outputs: [line('sar', 'SAR', '--ind2')], compute: (b, p) => ({ sar: m.sar(highs(b), lows(b), n(p, 'step'), n(p, 'max')) }),
  },
  { id: 'rsi', name: 'Relative Strength Index', pane: 'own', inputs: [int('length', 'Length', 14)], outputs: [line('rsi', 'RSI', '--ind1', 2)], levels: [30, 70], compute: (b, p) => ({ rsi: m.rsi(closes(b), n(p, 'length')) }) },
  {
    id: 'macd', name: 'MACD', pane: 'own', inputs: [int('fast', 'Fast', 12), int('slow', 'Slow', 26), int('signal', 'Signal', 9)],
    outputs: [{ key: 'hist', label: 'Histogram', color: '--up', kind: 'histogram' }, line('macd', 'MACD', '--ind1'), line('signal', 'Signal', '--ind2')],
    levels: [0],
    compute: (b, p) => ({ ...m.macd(closes(b), n(p, 'fast'), n(p, 'slow'), n(p, 'signal')) }),
  },
  {
    id: 'stoch', name: 'Stochastic', pane: 'own', inputs: [int('length', '%K length', 14), int('k', '%K smoothing', 3), int('d', '%D smoothing', 3)],
    outputs: [line('k', '%K', '--ind1'), line('d', '%D', '--ind2')], levels: [20, 80],
    compute: (b, p) => ({ ...m.stoch(highs(b), lows(b), closes(b), n(p, 'length'), n(p, 'k'), n(p, 'd')) }),
  },
  { id: 'atr', name: 'Average True Range', pane: 'own', inputs: [int('length', 'Length', 14)], outputs: [line('atr', 'ATR', '--ind2', 2)], compute: (b, p) => ({ atr: m.atr(highs(b), lows(b), closes(b), n(p, 'length')) }) },
  { id: 'adx', name: 'Average Directional Index', pane: 'own', inputs: [int('length', 'Length', 14)], outputs: [line('adx', 'ADX', '--ind3', 2)], levels: [25], compute: (b, p) => ({ adx: m.adx(highs(b), lows(b), closes(b), n(p, 'length')) }) },
  { id: 'obv', name: 'On Balance Volume', pane: 'own', inputs: [], outputs: [line('obv', 'OBV', '--ind1')], compute: (b) => ({ obv: m.obv(closes(b), volumes(b)) }) },
  { id: 'cci', name: 'Commodity Channel Index', pane: 'own', inputs: [int('length', 'Length', 20)], outputs: [line('cci', 'CCI', '--ind1')], levels: [-100, 100], compute: (b, p) => ({ cci: m.cci(highs(b), lows(b), closes(b), n(p, 'length')) }) },
  { id: 'willr', name: 'Williams %R', pane: 'own', inputs: [int('length', 'Length', 14)], outputs: [line('willr', '%R', '--ind3')], levels: [-80, -20], compute: (b, p) => ({ willr: m.willr(highs(b), lows(b), closes(b), n(p, 'length')) }) },
  { id: 'roc', name: 'Rate of Change', pane: 'own', inputs: [int('length', 'Length', 10)], outputs: [line('roc', 'ROC', '--ind2')], levels: [0], compute: (b, p) => ({ roc: m.roc(closes(b), n(p, 'length')) }) },
  { id: 'mfi', name: 'Money Flow Index', pane: 'own', inputs: [int('length', 'Length', 14)], outputs: [line('mfi', 'MFI', '--ind1')], levels: [20, 80], compute: (b, p) => ({ mfi: m.mfi(highs(b), lows(b), closes(b), volumes(b), n(p, 'length')) }) },
  { id: 'trix', name: 'TRIX', pane: 'own', inputs: [int('length', 'Length', 15)], outputs: [line('trix', 'TRIX', '--ind3')], levels: [0], compute: (b, p) => ({ trix: m.trix(closes(b), n(p, 'length')) }) },
  {
    id: 'aroon', name: 'Aroon', pane: 'own', inputs: [int('length', 'Length', 14)],
    outputs: [line('up', 'Up', '--up'), line('down', 'Down', '--dn')], levels: [50],
    compute: (b, p) => ({ ...m.aroon(highs(b), lows(b), n(p, 'length')) }),
  },
];

export function find(id: string): IndicatorDef | undefined {
  return REGISTRY.find((d) => d.id === id);
}

/** An indicator's settings with every input at its default. */
export function defaults(def: IndicatorDef): Params {
  return Object.fromEntries(def.inputs.map((i) => [i.key, i.default]));
}

/** The settings completed from the defaults and held inside each input's limits. A setting that is not a number where one is needed returns an error. */
export function validate(def: IndicatorDef, params: Params): { ok: true; params: Params } | { ok: false; error: string } {
  const out: Params = {};
  for (const input of def.inputs) {
    const raw = params[input.key] ?? input.default;
    if (input.kind === 'select') {
      if (!input.options?.includes(String(raw))) return { ok: false, error: `${input.label} must be one of ${input.options?.join(', ')}` };
      out[input.key] = String(raw);
      continue;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) return { ok: false, error: `${input.label} must be a number` };
    const held = Math.min(input.max ?? Infinity, Math.max(input.min ?? -Infinity, value));
    out[input.key] = input.kind === 'int' ? Math.round(held) : held;
  }
  return { ok: true, params: out };
}

/** The legend text of an indicator: `RSI 14` or `MACD 12 26 9`. */
export function caption(def: IndicatorDef, params: Params): string {
  const short = def.outputs[0].label.startsWith('%') || def.id === 'macd' || def.id === 'bb' ? def.name.split(' ').map((w) => w[0]).join('') : def.outputs[0].label;
  const label = def.id === 'macd' ? 'MACD' : def.id === 'bb' ? 'BB' : def.id === 'stoch' ? 'Stoch' : def.id === 'willr' ? '%R' : short;
  return [label, ...def.inputs.map((i) => String(params[i.key] ?? i.default))].join(' ');
}
