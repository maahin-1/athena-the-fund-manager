/** The indicators a formula can call: the ones the strategy engine (`athena.technicals.indicators.REGISTRY`) knows, with its parameter names, defaults and ranges
 *  and its line names. A test compares this table with the Python registry's own output, so the two cannot drift apart. */

export interface Setting {
  name: string;
  default: number;
  min: number;
  max: number;
  integer: boolean;
}

export interface IndicatorInfo {
  key: string;
  /** In the order a formula gives them: `macd(12, 26, 9)`. */
  params: Setting[];
  /** The lines, the first being the one a formula gets when it names none. */
  lines: string[];
  placement: 'overlay' | 'pane';
}

const whole = (name: string, def: number, min: number, max: number): Setting => ({ name, default: def, min, max, integer: true });
const real = (name: string, def: number, min: number, max: number): Setting => ({ name, default: def, min, max, integer: false });
const length = (def: number, min = 2, max = 100): Setting => whole('length', def, min, max);

export const INDICATORS: IndicatorInfo[] = [
  { key: 'sma', params: [length(50, 2, 400)], lines: ['value'], placement: 'overlay' },
  { key: 'ema', params: [length(20, 2, 400)], lines: ['value'], placement: 'overlay' },
  { key: 'wma', params: [length(20, 2, 400)], lines: ['value'], placement: 'overlay' },
  { key: 'bbands', params: [length(20, 5, 200), real('width', 2, 0.5, 4)], lines: ['upper', 'lower'], placement: 'overlay' },
  { key: 'sar', params: [real('acceleration', 0.02, 0.01, 0.2), real('maximum', 0.2, 0.1, 0.5)], lines: ['value'], placement: 'overlay' },
  { key: 'channel', params: [length(252, 5, 400)], lines: ['high', 'low'], placement: 'overlay' },
  { key: 'rsi', params: [length(14)], lines: ['value'], placement: 'pane' },
  { key: 'macd', params: [whole('fast', 12, 2, 100), whole('slow', 26, 3, 200), whole('signal', 9, 2, 100)], lines: ['macd', 'signal', 'histogram'], placement: 'pane' },
  { key: 'stoch', params: [whole('k', 14, 2, 100), whole('smooth', 3, 1, 20), whole('d', 3, 1, 20)], lines: ['k', 'd'], placement: 'pane' },
  { key: 'adx', params: [length(14)], lines: ['adx', 'plus_di', 'minus_di'], placement: 'pane' },
  { key: 'atr', params: [length(14)], lines: ['value'], placement: 'pane' },
  { key: 'obv', params: [], lines: ['value'], placement: 'pane' },
  { key: 'cci', params: [length(20, 5, 100)], lines: ['value'], placement: 'pane' },
  { key: 'mfi', params: [length(14)], lines: ['value'], placement: 'pane' },
  { key: 'willr', params: [length(14)], lines: ['value'], placement: 'pane' },
  { key: 'roc', params: [length(10, 1, 100)], lines: ['value'], placement: 'pane' },
];

export const INDICATOR = (key: string): IndicatorInfo | undefined => INDICATORS.find((i) => i.key === key.toLowerCase());
