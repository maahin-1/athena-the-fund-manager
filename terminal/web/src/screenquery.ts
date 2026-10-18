import { RANGE_FILTERS, type Limits, type Row } from './screenlogic';

/** The screen's query language: `pe < 20 and roe > 15 and (sector = "Technology" or index = "NIFTY 50")`. Pure: parse once, evaluate over every row.
 *
 *  Comparisons `< <= > >= = == != <>`, `between a and b`, `in (...)`, `not in (...)`, `is null`, `is not null`, `contains "text"`; `and`, `or`, `not`
 *  and brackets, `not` binding tightest and `or` loosest. Keywords and field names are not case sensitive. A figure that is missing meets no limit,
 *  whichever way it points (use `is null` to find it); `not` turns that into a yes, so ask for a figure with `is not null`. Percentage fields are written
 *  in percent, with or without the sign (`roe > 15` and `roe > 15%` are the same); a sign on another field is an error. */

export type FieldKind = 'number' | 'percent' | 'text' | 'set';

export interface FieldDef {
  /** Every name the query accepts for it; the first is the stored column's name (the set field's column is `indices`). */
  names: string[];
  column: string;
  label: string;
  kind: FieldKind;
  unit: string;
  /** A query that uses it, shown in the field list and kept parsing by a test. */
  example: string;
}

const f = (names: string[], label: string, kind: FieldKind, example: string, unit = '', column = names[0]): FieldDef => ({ names, column, label, kind, unit, example });

export const FIELDS: FieldDef[] = [
  f(['symbol'], 'Symbol', 'text', 'symbol = "TCS"'),
  f(['name'], 'Name', 'text', 'name contains "bank"'),
  f(['kind', 'type'], 'Type', 'text', 'kind = "stock"'),
  f(['sector'], 'Sector', 'text', 'sector = "Technology"'),
  f(['industry'], 'Industry (the exchange\'s, for stocks in its indices)', 'text', 'industry = "Banks"'),
  f(['index', 'indices'], 'Index membership', 'set', 'index = "NIFTY 50"', '', 'indices'),
  f(['trend'], 'Trend', 'text', 'trend = "aligned_up"'),
  f(['close', 'price'], 'Close', 'number', 'close > 100', 'Rs'),
  f(['ret_1d'], '1-day return', 'percent', 'ret_1d > 2', '%'),
  f(['ret_1m'], '1-month return', 'percent', 'ret_1m > 5', '%'),
  f(['ret_3m'], '3-month return', 'percent', 'ret_3m > 10', '%'),
  f(['ret_6m'], '6-month return', 'percent', 'ret_6m > 10', '%'),
  f(['ret_1y'], '1-year return', 'percent', 'ret_1y > 20', '%'),
  f(['from_52w_high', 'from_high'], 'From 52-week high', 'percent', 'from_52w_high > -10', '%'),
  f(['from_52w_low', 'from_low'], 'Above 52-week low', 'percent', 'from_52w_low < 20', '%'),
  f(['rsi_14', 'rsi'], 'RSI (14)', 'number', 'rsi < 30'),
  f(['sma50_vs_200'], '50-day average against 200-day', 'percent', 'sma50_vs_200 > 0', '%'),
  f(['market_cap_cr', 'mcap'], 'Market cap', 'number', 'mcap > 50000', 'Rs cr'),
  f(['traded_value_cr', 'traded'], 'Traded per day', 'number', 'traded > 10', 'Rs cr'),
  f(['pe'], 'P/E', 'number', 'pe < 20', 'x'),
  f(['pb'], 'P/B', 'number', 'pb < 3', 'x'),
  f(['roe'], 'ROE', 'percent', 'roe > 15%', '%'),
  f(['net_margin', 'margin'], 'Net margin', 'percent', 'net_margin > 10', '%'),
  f(['debt_to_equity', 'de'], 'Debt to equity', 'number', 'debt_to_equity < 0.5', 'x'),
  f(['dividend_yield', 'yield'], 'Dividend yield', 'percent', 'dividend_yield > 3%', '%'),
  f(['earnings_yield'], 'Earnings yield', 'percent', 'earnings_yield > 5', '%'),
  f(['revenue_growth', 'growth'], 'Sales growth (quarter, year on year)', 'percent', 'revenue_growth > 15', '%'),
  f(['flags'], 'Data flags', 'number', 'flags = 0'),
];

const BY_NAME = new Map(FIELDS.flatMap((d) => d.names.map((n) => [n.toLowerCase(), d] as const)));

export interface QueryError {
  message: string;
  /** Where in the text, counted in characters, and how many characters it covers. */
  at: number;
  length: number;
}

export type Node =
  | { t: 'and' | 'or'; a: Node; b: Node }
  | { t: 'not'; a: Node }
  | { t: 'cmp'; field: FieldDef; op: '<' | '<=' | '>' | '>=' | '=' | '!=' | 'between' | 'in' | 'notin' | 'contains'; values: (number | string)[] }
  | { t: 'null'; field: FieldDef; not: boolean };

export type Parsed = { ok: true; node: Node | null } | { ok: false; error: QueryError };

interface Tok {
  k: 'num' | 'str' | 'word' | 'op' | 'lp' | 'rp' | 'comma' | 'pct' | 'minus' | 'end';
  v: string;
  at: number;
  len: number;
}

class Fail extends Error {
  constructor(public error: QueryError) {
    super(error.message);
  }
}
const fail = (message: string, at: number, length = 1): never => {
  throw new Fail({ message, at, length });
};

function tokenize(text: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (/\s/.test(c)) {
      i++;
    } else if (/[0-9]/.test(c)) {
      let j = i;
      while (j < text.length && /[0-9.]/.test(text[j])) j++;
      out.push({ k: 'num', v: text.slice(i, j), at: i, len: j - i });
      i = j;
    } else if (/[A-Za-z_]/.test(c)) {
      let j = i;
      while (j < text.length && /[A-Za-z0-9_]/.test(text[j])) j++;
      out.push({ k: 'word', v: text.slice(i, j), at: i, len: j - i });
      i = j;
    } else if (c === '"' || c === "'") {
      const close = text.indexOf(c, i + 1);
      if (close < 0) fail('text is not closed: add the closing quote', i, text.length - i);
      out.push({ k: 'str', v: text.slice(i + 1, close), at: i, len: close + 1 - i });
      i = close + 1;
    } else if ('<>=!'.includes(c)) {
      const two = text.slice(i, i + 2);
      const op = ['<=', '>=', '!=', '<>', '=='].includes(two) ? two : ['<', '>', '='].includes(c) ? c : '';
      if (!op) fail(`unexpected character "${c}"`, i);
      out.push({ k: 'op', v: op, at: i, len: op.length });
      i += op.length;
    } else if (c === '(') out.push({ k: 'lp', v: c, at: i++, len: 1 });
    else if (c === ')') out.push({ k: 'rp', v: c, at: i++, len: 1 });
    else if (c === ',') out.push({ k: 'comma', v: c, at: i++, len: 1 });
    else if (c === '%') out.push({ k: 'pct', v: c, at: i++, len: 1 });
    else if (c === '-') out.push({ k: 'minus', v: c, at: i++, len: 1 });
    else fail(`unexpected character "${c}"`, i);
  }
  out.push({ k: 'end', v: '', at: text.length, len: 0 });
  return out;
}

const KEYWORDS = new Set(['and', 'or', 'not', 'between', 'in', 'is', 'null', 'contains']);
const isWord = (t: Tok, word: string): boolean => t.k === 'word' && t.v.toLowerCase() === word;

function distance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let prev = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const keep = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = keep;
    }
  }
  return row[b.length];
}

function nearest(name: string): string | null {
  let best: [number, string] | null = null;
  for (const known of BY_NAME.keys()) {
    const d = distance(name.toLowerCase(), known);
    if (d <= 2 && (!best || d < best[0])) best = [d, known];
  }
  return best ? best[1] : null;
}

class Parser {
  private i = 0;
  constructor(private tokens: Tok[]) {}

  private get peek(): Tok {
    return this.tokens[this.i];
  }

  private next(): Tok {
    return this.tokens[this.i++];
  }

  parse(): Node | null {
    if (this.peek.k === 'end') return null;
    const node = this.or();
    const t = this.peek;
    if (t.k !== 'end') {
      if (t.k === 'rp') fail('unexpected ")"', t.at, 1);
      if (t.k === 'word' && !KEYWORDS.has(t.v.toLowerCase())) fail(`unexpected "${t.v}"; put and or or between conditions`, t.at, t.len);
      fail(`unexpected "${t.v}"`, t.at, Math.max(1, t.len));
    }
    return node;
  }

  private or(): Node {
    let a = this.and();
    while (isWord(this.peek, 'or')) {
      this.next();
      a = { t: 'or', a, b: this.and() };
    }
    return a;
  }

  private and(): Node {
    let a = this.not();
    while (isWord(this.peek, 'and')) {
      this.next();
      a = { t: 'and', a, b: this.not() };
    }
    return a;
  }

  private not(): Node {
    if (isWord(this.peek, 'not')) {
      this.next();
      return { t: 'not', a: this.not() };
    }
    return this.condition();
  }

  private condition(): Node {
    const t = this.peek;
    if (t.k === 'lp') {
      this.next();
      const inner = this.or();
      if (this.peek.k !== 'rp') fail('missing ")"', this.peek.at, 1);
      this.next();
      return inner;
    }
    if (t.k === 'end') return fail(`expected a condition after ${this.tokens[this.i - 1]?.v ?? 'the start'}`, t.at, 0);
    if (t.k !== 'word' || KEYWORDS.has(t.v.toLowerCase())) return fail(`expected a condition, found "${t.v}"`, t.at, Math.max(1, t.len));
    const field = BY_NAME.get(t.v.toLowerCase());
    if (!field) {
      const guess = nearest(t.v);
      return fail(`unknown field "${t.v}"${guess ? `; did you mean "${guess}"?` : ''}`, t.at, t.len);
    }
    this.next();
    const op = this.peek;
    const name = t.v;
    if (op.k === 'end') return fail(`expected a comparison after ${name}`, op.at, 0);
    if (op.k === 'op') {
      this.next();
      const symbol = op.v === '==' ? '=' : op.v === '<>' ? '!=' : (op.v as '<' | '<=' | '>' | '>=' | '=' | '!=');
      if (field.kind === 'text' || field.kind === 'set') {
        if (!['=', '!='].includes(symbol)) return fail(`${name} is text; use =, !=, in or contains`, op.at, op.len);
      }
      return { t: 'cmp', field, op: symbol, values: [this.value(field, name)] };
    }
    if (isWord(op, 'between')) {
      this.next();
      const lo = this.value(field, name);
      if (!isWord(this.peek, 'and')) fail('expected "and" in between', this.peek.at, Math.max(1, this.peek.len));
      this.next();
      return { t: 'cmp', field, op: 'between', values: [lo, this.value(field, name)] };
    }
    if (isWord(op, 'in') || isWord(op, 'not')) {
      const negate = isWord(op, 'not');
      this.next();
      if (negate) {
        if (!isWord(this.peek, 'in')) fail('expected "in" after not', this.peek.at, Math.max(1, this.peek.len));
        this.next();
      }
      if (this.peek.k !== 'lp') fail('expected "(" after in', this.peek.at, Math.max(1, this.peek.len));
      this.next();
      const values: (number | string)[] = [];
      for (;;) {
        if (this.peek.k === 'rp' || this.peek.k === 'end') {
          if (!values.length) fail('expected a number or text in the list', this.peek.at, Math.max(1, this.peek.len));
        }
        values.push(this.value(field, name));
        if (this.peek.k === 'comma') {
          this.next();
          continue;
        }
        break;
      }
      if (this.peek.k !== 'rp') fail('missing ")"', this.peek.at, Math.max(1, this.peek.len));
      this.next();
      return { t: 'cmp', field, op: negate ? 'notin' : 'in', values };
    }
    if (isWord(op, 'is')) {
      this.next();
      let not = false;
      if (isWord(this.peek, 'not')) {
        this.next();
        not = true;
      }
      if (!isWord(this.peek, 'null')) fail('expected "null" after is', this.peek.at, Math.max(1, this.peek.len));
      this.next();
      return { t: 'null', field, not };
    }
    if (isWord(op, 'contains')) {
      if (field.kind !== 'text') return fail(`contains is for text fields; ${name} is ${field.kind === 'set' ? 'a set (use = or in)' : 'a number'}`, op.at, op.len);
      this.next();
      return { t: 'cmp', field, op: 'contains', values: [this.value(field, name)] };
    }
    return fail(`expected a comparison after ${name}, found "${op.v}"`, op.at, Math.max(1, op.len));
  }

  /** One value for a field: a number (in the field's percent for a percentage field, stored as a fraction) or text. */
  private value(field: FieldDef, name: string): number | string {
    let t = this.peek;
    let sign = 1;
    if (t.k === 'minus') {
      this.next();
      sign = -1;
      t = this.peek;
    }
    if (field.kind === 'number' || field.kind === 'percent') {
      if (t.k !== 'num') return fail(t.k === 'str' ? `${name} takes a number, not text` : 'expected a number', t.at, Math.max(1, t.len));
      this.next();
      const n = Number(t.v);
      if (!Number.isFinite(n)) return fail(`${t.v} is not a number`, t.at, t.len);
      if (this.peek.k === 'pct') {
        if (field.kind !== 'percent') return fail(`${name} is not a percentage`, this.peek.at, 1);
        this.next();
      }
      return (sign * n) / (field.kind === 'percent' ? 100 : 1);
    }
    if (sign < 0 || t.k === 'num') return fail(`${name} is text; put the value in quotes`, t.at, Math.max(1, t.len));
    if (t.k === 'str') {
      this.next();
      return t.v;
    }
    if (t.k === 'word' && !KEYWORDS.has(t.v.toLowerCase())) {
      this.next();
      return t.v;
    }
    return fail('expected text', t.at, Math.max(1, t.len));
  }
}

/** Parse a query. Empty text is a query that matches every row (`node: null`). */
export function parseQuery(text: string): Parsed {
  try {
    return { ok: true, node: new Parser(tokenize(text)).parse() };
  } catch (e) {
    if (e instanceof Fail) return { ok: false, error: e.error };
    throw e;
  }
}

const EPS = 1e-9;

function holds(node: Extract<Node, { t: 'cmp' }>, row: Row): boolean {
  const { field, op, values } = node;
  const cell = row[field.column];
  if (field.kind === 'set') {
    const members = typeof cell === 'string' && cell ? cell.split('|').map((m) => m.toLowerCase()) : [];
    const wanted = values.map((v) => String(v).toLowerCase());
    if (op === '=' || op === 'in') return wanted.some((w) => members.includes(w));
    return !wanted.some((w) => members.includes(w)); // != and not in: a member of none of them, an empty set included
  }
  if (field.kind === 'text') {
    if (typeof cell !== 'string' || cell === '') return false;
    const have = cell.toLowerCase();
    const wanted = values.map((v) => String(v).toLowerCase());
    switch (op) {
      case '=':
      case 'in':
        return wanted.includes(have);
      case '!=':
      case 'notin':
        return !wanted.includes(have);
      case 'contains':
        return have.includes(wanted[0]);
      default:
        return false;
    }
  }
  if (typeof cell !== 'number' || !Number.isFinite(cell)) return false; // no figure meets no limit
  const [a, b] = values as number[];
  switch (op) {
    case '<':
      return cell < a;
    case '<=':
      return cell <= a + EPS;
    case '>':
      return cell > a;
    case '>=':
      return cell >= a - EPS;
    case '=':
      return Math.abs(cell - a) < EPS;
    case '!=':
      return Math.abs(cell - a) >= EPS;
    case 'between':
      return cell >= a - EPS && cell <= b + EPS;
    case 'in':
      return (values as number[]).some((v) => Math.abs(cell - v) < EPS);
    case 'notin':
      return !(values as number[]).some((v) => Math.abs(cell - v) < EPS);
    default:
      return false;
  }
}

export function evaluate(node: Node | null, row: Row): boolean {
  if (!node) return true;
  switch (node.t) {
    case 'and':
      return evaluate(node.a, row) && evaluate(node.b, row);
    case 'or':
      return evaluate(node.a, row) || evaluate(node.b, row);
    case 'not':
      return !evaluate(node.a, row);
    case 'null': {
      const cell = row[node.field.column];
      const missing = cell === null || cell === undefined || cell === '';
      return node.not ? !missing : missing;
    }
    case 'cmp':
      return holds(node, row);
  }
}

/** The filters' numeric ranges written as a query, in the units the page shows (`roe >= 15`, not 0.15), for the presets to display and be edited. */
export function limitsToQuery(limits: Limits): string {
  const parts: string[] = [];
  for (const [column, [lo, hi]] of Object.entries(limits)) {
    const factor = RANGE_FILTERS[column]?.[2] ?? 1;
    const shown = (n: number): string => String(Math.round((n / factor) * 1e6) / 1e6);
    if (lo !== null && hi !== null) parts.push(`${column} between ${shown(lo)} and ${shown(hi)}`);
    else if (lo !== null) parts.push(`${column} >= ${shown(lo)}`);
    else if (hi !== null) parts.push(`${column} <= ${shown(hi)}`);
  }
  return parts.join(' and ');
}
