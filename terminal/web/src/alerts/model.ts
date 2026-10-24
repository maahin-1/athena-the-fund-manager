import type { Quote } from '../feed';

/** One test of a quote. Prices are in rupees; `pct` is a percentage (2 means 2%). */
export type Condition =
  | { kind: 'price_above'; value: number }
  | { kind: 'price_below'; value: number }
  | { kind: 'change_above'; pct: number } // up at least this much on the previous close
  | { kind: 'change_below'; pct: number } // down at least this much (give a positive number)
  | { kind: 'volume_above'; value: number }
  | { kind: 'near_52w_high'; pct: number } // within this much of the 52-week high
  | { kind: 'near_52w_low'; pct: number };

export interface Alert {
  id: string;
  symbol: string;
  conditions: Condition[];
  /** `all` conditions must hold together; `any` needs one. */
  match: 'all' | 'any';
  /** `once`: fires one time and is then done. `repeat`: fires each time it becomes true again after being false, no oftener than `cooldownMs`. */
  mode: 'once' | 'repeat';
  cooldownMs: number;
  note: string;
  created: number;
  /** When it last fired (ms since 1970), or null. */
  firedAt: number | null;
  done: boolean;
  /** Whether it held on the last quote seen: a fire needs a change from false to true, so an alert made while its condition already holds waits for it to stop and start again. */
  held: boolean | null;
  /** Where to send it besides the page; each is optional. */
  channels: { browser: boolean; sound: boolean; webhook: string | null };
}

export const DEFAULT_COOLDOWN_MS = 15 * 60_000;

export function describeCondition(c: Condition): string {
  switch (c.kind) {
    case 'price_above': return `price above ${c.value}`;
    case 'price_below': return `price below ${c.value}`;
    case 'change_above': return `up ${c.pct}% or more on the day`;
    case 'change_below': return `down ${c.pct}% or more on the day`;
    case 'volume_above': return `volume above ${c.value.toLocaleString('en-IN')}`;
    case 'near_52w_high': return `within ${c.pct}% of the 52-week high`;
    case 'near_52w_low': return `within ${c.pct}% of the 52-week low`;
  }
}

export function describe(a: Alert): string {
  const joiner = a.match === 'all' ? ' and ' : ' or ';
  return `${a.symbol}: ${a.conditions.map(describeCondition).join(joiner)}`;
}

/** Whether one condition holds on a quote. A figure the quote lacks makes the condition not hold, never true. */
export function holds(c: Condition, q: Quote): boolean {
  switch (c.kind) {
    case 'price_above': return q.l > c.value;
    case 'price_below': return q.l < c.value;
    case 'change_above': return q.pc > 0 && (q.l / q.pc - 1) * 100 >= c.pct;
    case 'change_below': return q.pc > 0 && (1 - q.l / q.pc) * 100 >= c.pct;
    case 'volume_above': return q.v !== null && q.v > c.value;
    case 'near_52w_high': return q.h52 !== null && q.h52 > 0 && (1 - q.l / q.h52) * 100 <= c.pct;
    case 'near_52w_low': return q.l52 !== null && q.l52 > 0 && (q.l / q.l52 - 1) * 100 <= c.pct;
  }
}

export function allHold(a: Alert, q: Quote): boolean {
  if (a.conditions.length === 0) return false;
  return a.match === 'all' ? a.conditions.every((c) => holds(c, q)) : a.conditions.some((c) => holds(c, q));
}

export interface Fired {
  alert: Alert;
  message: string;
  at: number;
}

/** Applies one quote to the alerts for its symbol. Returns the alerts as they now stand and the ones that fired. An alert fires on the change from not holding to holding (the first quote only sets
 * the starting state), is done after firing when `once`, and waits out its cooldown when `repeat`. */
export function step(alerts: readonly Alert[], q: Quote, now: number): { alerts: Alert[]; fired: Fired[] } {
  const fired: Fired[] = [];
  const next = alerts.map((a) => {
    if (a.symbol !== q.s || a.done) return a;
    const holding = allHold(a, q);
    const rising = holding && a.held === false;
    const cooled = a.firedAt === null || now - a.firedAt >= a.cooldownMs;
    if (rising && cooled) {
      fired.push({ alert: a, at: now, message: `${describe(a)} (price ${q.l})${a.note ? ` · ${a.note}` : ''}` });
      return { ...a, held: true, firedAt: now, done: a.mode === 'once' };
    }
    return a.held === holding ? a : { ...a, held: holding };
  });
  return { alerts: next, fired };
}

let counter = 0;
export function makeAlert(symbol: string, conditions: Condition[], options: Partial<Pick<Alert, 'match' | 'mode' | 'cooldownMs' | 'note' | 'channels'>> = {}, now = Date.now()): Alert {
  counter += 1;
  return {
    id: `a${now.toString(36)}${counter}`,
    symbol: symbol.toUpperCase(),
    conditions,
    match: options.match ?? 'all',
    mode: options.mode ?? 'once',
    cooldownMs: options.cooldownMs ?? DEFAULT_COOLDOWN_MS,
    note: options.note ?? '',
    created: now,
    firedAt: null,
    done: false,
    held: null,
    channels: options.channels ?? { browser: true, sound: true, webhook: null },
  };
}

const KINDS = new Set<Condition['kind']>(['price_above', 'price_below', 'change_above', 'change_below', 'volume_above', 'near_52w_high', 'near_52w_low']);

/** Alerts read back from storage: anything that is not an alert is dropped, never guessed at. */
export function readAlerts(raw: unknown): Alert[] {
  if (!Array.isArray(raw)) return [];
  const out: Alert[] = [];
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue;
    const a = item as Partial<Alert>;
    const conditions = Array.isArray(a.conditions) ? a.conditions.filter((c): c is Condition => typeof c === 'object' && c !== null && KINDS.has((c as Condition).kind)) : [];
    if (typeof a.id !== 'string' || typeof a.symbol !== 'string' || conditions.length === 0) continue;
    const webhook = a.channels && typeof a.channels.webhook === 'string' && /^https?:\/\//.test(a.channels.webhook) ? a.channels.webhook : null;
    out.push({
      id: a.id, symbol: a.symbol, conditions, match: a.match === 'any' ? 'any' : 'all', mode: a.mode === 'repeat' ? 'repeat' : 'once',
      cooldownMs: typeof a.cooldownMs === 'number' && a.cooldownMs >= 0 ? a.cooldownMs : DEFAULT_COOLDOWN_MS, note: typeof a.note === 'string' ? a.note : '',
      created: typeof a.created === 'number' ? a.created : 0, firedAt: typeof a.firedAt === 'number' ? a.firedAt : null, done: a.done === true, held: null,
      channels: { browser: a.channels?.browser !== false, sound: a.channels?.sound !== false, webhook },
    });
  }
  return out;
}
