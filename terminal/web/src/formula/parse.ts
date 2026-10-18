import { nearest } from '../similar';
import { MAX_CONDITIONS, MAX_DEPTH, MAX_NODES, MAX_SHIFT, MAX_WINDOW, FormulaError, type Cond, type CompareOp, type Compiled, type Expr, type PriceField } from './ast';
import { INDICATOR, INDICATORS } from './catalogue';

/** Text to the strategy tree. A formula is one expression: a number-valued one is an indicator, a yes-or-no one is a condition. The grammar and the limits are
 *  in docs/superpowers/specs/2026-10-18-formula-language-design.md. Nothing here runs anything: it reads text and builds data. */

export type Result = { ok: true; value: Compiled } | { ok: false; error: FormulaError };

interface Tok {
  k: 'num' | 'word' | 'op' | 'lp' | 'rp' | 'lb' | 'rb' | 'comma' | 'dot' | 'eq' | 'end';
  v: string;
  at: number;
  len: number;
}

type Val = { t: 'num'; e: Expr; at: number; end: number; callable?: string } | { t: 'bool'; c: Cond; at: number; end: number };

const PRICES: Record<string, PriceField> = { open: 'open', high: 'high', low: 'low', close: 'close', volume: 'volume', o: 'open', h: 'high', l: 'low', c: 'close', v: 'volume' };
const ROLLING: Record<string, 'max' | 'min' | 'mean'> = { highest: 'max', lowest: 'min', average: 'mean' };
const KEYWORDS = new Set(['and', 'or', 'not', 'crosses', 'above', 'below']);
const FUNCTIONS = [...INDICATORS.map((i) => i.key), ...Object.keys(ROLLING), 'crossover', 'crossunder'];
const COMPARE: Record<string, CompareOp> = { '>': 'gt', '>=': 'ge', '<': 'lt', '<=': 'le' };

const fail = (message: string, at: number, length = 1): never => {
  throw new FormulaError(message, at, Math.max(1, length));
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
      while (j < text.length && /[0-9]/.test(text[j])) j++;
      if (text[j] === '.' && /[0-9]/.test(text[j + 1] ?? '')) {
        j++;
        while (j < text.length && /[0-9]/.test(text[j])) j++;
      }
      out.push({ k: 'num', v: text.slice(i, j), at: i, len: j - i });
      i = j;
    } else if (/[A-Za-z_]/.test(c)) {
      let j = i;
      while (j < text.length && /[A-Za-z0-9_]/.test(text[j])) j++;
      out.push({ k: 'word', v: text.slice(i, j), at: i, len: j - i });
      i = j;
    } else if (c === '>' || c === '<') {
      const two = text[i + 1] === '=' ? c + '=' : c;
      out.push({ k: 'op', v: two, at: i, len: two.length });
      i += two.length;
    } else if ('+-*/'.includes(c)) out.push({ k: 'op', v: c, at: i++, len: 1 });
    else if (c === '(') out.push({ k: 'lp', v: c, at: i++, len: 1 });
    else if (c === ')') out.push({ k: 'rp', v: c, at: i++, len: 1 });
    else if (c === '[') out.push({ k: 'lb', v: c, at: i++, len: 1 });
    else if (c === ']') out.push({ k: 'rb', v: c, at: i++, len: 1 });
    else if (c === ',') out.push({ k: 'comma', v: c, at: i++, len: 1 });
    else if (c === '.') out.push({ k: 'dot', v: c, at: i++, len: 1 });
    else if (c === '=') out.push({ k: 'eq', v: c, at: i++, len: 1 });
    else fail(`unexpected character "${c}"`, i);
  }
  out.push({ k: 'end', v: '', at: text.length, len: 0 });
  return out;
}

const isWord = (t: Tok, word: string): boolean => t.k === 'word' && t.v.toLowerCase() === word;

class Parser {
  private i = 0;
  constructor(private text: string, private tokens: Tok[]) {}

  private get peek(): Tok {
    return this.tokens[this.i];
  }

  private next(): Tok {
    return this.tokens[this.i++];
  }

  /** The kind of the next token, read afresh (the compiler cannot narrow it across a loop). */
  private kind(): Tok['k'] {
    return this.tokens[this.i].k;
  }

  parse(): Compiled {
    if (this.peek.k === 'end') fail('type a formula, for example close > sma(200)', 0, 1);
    const v = this.or();
    const t = this.peek;
    if (t.k !== 'end') {
      if (t.k === 'rp') fail('unexpected ")"', t.at, 1);
      fail(`unexpected "${t.v}"`, t.at, t.len);
    }
    const result: Compiled = v.t === 'num' ? { kind: 'expr', tree: v.e } : { kind: 'cond', tree: v.c };
    this.limits(result);
    return result;
  }

  // ---- limits of the strategy model, counted the way its parser counts them
  private limits(result: Compiled): void {
    let nodes = 0;
    const visit = (node: Cond | Expr, depth: number): void => {
      nodes++;
      if (nodes > MAX_NODES) fail(`the formula has more than ${MAX_NODES} parts`, 0, this.text.length);
      if (depth > MAX_DEPTH) fail(`the formula is nested more than ${MAX_DEPTH} levels deep`, 0, this.text.length);
      if ('all' in node) node.all.forEach((c) => visit(c, depth + 1));
      else if ('any' in node) node.any.forEach((c) => visit(c, depth + 1));
      else if ('not' in node) visit(node.not, depth + 1);
      else if ('op' in node) {
        visit(node.left, depth + 1);
        visit(node.right, depth + 1);
      } else if ('rolling' in node) visit(node.of, depth + 1);
      else if ('arith' in node) {
        visit(node.left, depth + 1);
        visit(node.right, depth + 1);
      }
    };
    visit(result.tree, 1);
  }

  private slice(v: Val): string {
    return this.text.slice(v.at, v.end).trim();
  }

  private num(v: Val): Extract<Val, { t: 'num' }> {
    if (v.t === 'bool') fail('a comparison is yes or no, not a number', v.at, v.end - v.at);
    return v as Extract<Val, { t: 'num' }>;
  }

  private bool(v: Val, word: string): Extract<Val, { t: 'bool' }> {
    if (v.t === 'num') fail(`${word} joins yes/no conditions, and ${this.slice(v)} is a number`, v.at, v.end - v.at);
    return v as Extract<Val, { t: 'bool' }>;
  }

  private or(): Val {
    let left = this.and();
    while (isWord(this.peek, 'or')) {
      this.next();
      const l = this.bool(left, 'or');
      const r = this.bool(this.and(), 'or');
      left = { t: 'bool', c: join('any', l.c, r.c, l.at), at: l.at, end: r.end };
      this.checkWidth(left.c, l.at);
    }
    return left;
  }

  private and(): Val {
    let left = this.not();
    while (isWord(this.peek, 'and')) {
      this.next();
      const l = this.bool(left, 'and');
      const r = this.bool(this.not(), 'and');
      left = { t: 'bool', c: join('all', l.c, r.c, l.at), at: l.at, end: r.end };
      this.checkWidth(left.c, l.at);
    }
    return left;
  }

  private checkWidth(c: Cond, at: number): void {
    const items = 'all' in c ? c.all : 'any' in c ? c.any : null;
    if (items && items.length > MAX_CONDITIONS) fail(`more than ${MAX_CONDITIONS} conditions joined by ${'all' in c ? 'and' : 'or'}`, at, this.text.length - at);
  }

  private not(): Val {
    const t = this.peek;
    if (isWord(t, 'not')) {
      this.next();
      const v = this.not();
      if (v.t === 'num') return fail('not needs a yes/no condition', v.at, v.end - v.at);
      return { t: 'bool', c: { not: v.c }, at: t.at, end: v.end };
    }
    return this.compare();
  }

  private compare(): Val {
    const left = this.sum();
    const t = this.peek;
    if (t.k === 'op' && t.v in COMPARE) {
      this.next();
      const l = this.num(left);
      const r = this.num(this.sum(`expected a value after ${t.v}`));
      return { t: 'bool', c: { op: COMPARE[t.v], left: l.e, right: r.e }, at: l.at, end: r.end };
    }
    if (isWord(t, 'crosses')) {
      this.next();
      const w = this.peek;
      if (!(isWord(w, 'above') || isWord(w, 'below'))) fail('expected "above" or "below" after crosses', w.at, w.len);
      this.next();
      const l = this.num(left);
      const r = this.num(this.sum(`expected a value after ${w.v}`));
      return { t: 'bool', c: { op: isWord(w, 'above') ? 'crosses_above' : 'crosses_below', left: l.e, right: r.e }, at: l.at, end: r.end };
    }
    return left;
  }

  private sum(after?: string): Val {
    let left = this.term(after);
    while (this.peek.k === 'op' && (this.peek.v === '+' || this.peek.v === '-')) {
      const op = this.next();
      const l = this.num(left);
      const r = this.num(this.term(`expected a value after ${op.v}`));
      left = { t: 'num', e: { arith: op.v === '+' ? 'add' : 'sub', left: l.e, right: r.e }, at: l.at, end: r.end };
    }
    return left;
  }

  private term(after?: string): Val {
    let left = this.unary(after);
    while (this.peek.k === 'op' && (this.peek.v === '*' || this.peek.v === '/')) {
      const op = this.next();
      const l = this.num(left);
      const r = this.num(this.unary(`expected a value after ${op.v}`));
      if (op.v === '/' && 'const' in r.e && r.e.const === 0) fail('cannot divide by zero', r.at, r.end - r.at);
      left = { t: 'num', e: { arith: op.v === '*' ? 'mul' : 'div', left: l.e, right: r.e }, at: l.at, end: r.end };
    }
    return left;
  }

  private unary(after?: string): Val {
    const t = this.peek;
    if (t.k === 'op' && t.v === '-') {
      this.next();
      const v = this.num(this.postfix(`expected a value after -`));
      if ('const' in v.e) return { t: 'num', e: { const: -v.e.const }, at: t.at, end: v.end };
      return { t: 'num', e: { arith: 'sub', left: { const: 0 }, right: v.e }, at: t.at, end: v.end };
    }
    return this.postfix(after);
  }

  private postfix(after?: string): Val {
    let v = this.primary(after);
    let fresh = v.t === 'num' && v.callable !== undefined; // a line may be named straight after an indicator call
    for (;;) {
      const t = this.peek;
      if (t.k === 'lb') {
        this.next();
        const n = this.peek;
        if (v.t === 'bool') fail('a yes/no condition cannot be shifted', v.at, v.end - v.at);
        if (n.k !== 'num') fail(`a shift must be a whole number from 0 to ${MAX_SHIFT}`, n.at, n.len);
        this.next();
        const amount = Number(n.v);
        if (!Number.isInteger(amount) || amount > MAX_SHIFT) fail(`a shift must be a whole number from 0 to ${MAX_SHIFT}`, n.at, n.len);
        const close = this.peek;
        if (close.k !== 'rb') fail('missing "]"', close.at, 1);
        this.next();
        const shiftedExpr = shifted((v as Extract<Val, { t: 'num' }>).e, amount, v);
        v = { ...(v as Extract<Val, { t: 'num' }>), e: shiftedExpr, end: close.at + 1 };
        fresh = false;
      } else if (t.k === 'dot') {
        this.next();
        const name = this.peek;
        if (v.t === 'bool' || !fresh) fail('only an indicator has lines', t.at, 1 + name.len);
        if (name.k !== 'word') fail('expected a line name after "."', name.at, name.len);
        this.next();
        const info = INDICATOR((v as Extract<Val, { t: 'num' }>).callable!)!;
        if (!info.lines.includes(name.v.toLowerCase())) fail(`${info.key} has the line${info.lines.length > 1 ? 's' : ''} ${info.lines.join(', ')}`, name.at, name.len);
        (v as Extract<Val, { t: 'num' }>).e = { ...((v as Extract<Val, { t: 'num' }>).e as { ind: string; params: Record<string, number>; line: string }), line: name.v.toLowerCase() };
        v.end = name.at + name.len;
        fresh = false;
      } else break;
    }
    return v;
  }

  private primary(after?: string): Val {
    const t = this.peek;
    if (t.k === 'num') {
      this.next();
      return { t: 'num', e: { const: Number(t.v) }, at: t.at, end: t.at + t.len };
    }
    if (t.k === 'lp') {
      this.next();
      const inner = this.or();
      const close = this.peek;
      if (close.k !== 'rp') fail('missing ")"', close.at, 1);
      this.next();
      return { ...inner, at: t.at, end: close.at + 1 };
    }
    if (t.k === 'word' && !KEYWORDS.has(t.v.toLowerCase())) {
      const name = t.v.toLowerCase();
      if (this.tokens[this.i + 1]?.k === 'lp') return this.call(name, t);
      this.next();
      if (name in PRICES) return { t: 'num', e: { price: PRICES[name] }, at: t.at, end: t.at + t.len };
      const guess = nearest(name, Object.keys(PRICES));
      return fail(`unknown name "${t.v}"${guess ? `; did you mean "${guess}"?` : ''}`, t.at, t.len);
    }
    if (t.k === 'end') return fail(after ?? 'expected a value', t.at, 1);
    if (t.k === 'word') return fail(`expected a value, found "${t.v}"`, t.at, t.len);
    return fail(after ?? 'expected a value', t.at, t.len);
  }

  private call(name: string, t: Tok): Val {
    if (name in ROLLING) return this.rolling(name, t);
    if (name === 'crossover' || name === 'crossunder') return this.cross(name, t);
    const info = INDICATOR(name);
    if (!info) {
      const guess = nearest(name, FUNCTIONS);
      return fail(`unknown function "${t.v}"${guess ? `; did you mean "${guess}"?` : ''}`, t.at, t.len);
    }
    this.next(); // the name
    this.next(); // "("
    const values: Record<string, number> = {};
    let position = 0;
    while (this.peek.k !== 'rp') {
      const a = this.peek;
      let setting = info.params[position];
      let named = false;
      if (a.k === 'word' && this.tokens[this.i + 1]?.k === 'eq') {
        setting = info.params.find((p) => p.name === a.v.toLowerCase())!;
        if (!setting) fail(`${info.key} has no setting "${a.v}"; it has ${info.params.map((p) => p.name).join(', ') || 'none'}`, a.at, a.len);
        this.next();
        this.next();
        named = true;
      } else {
        position++;
        if (!setting) fail(`${info.key} takes at most ${info.params.length} setting${info.params.length === 1 ? '' : 's'}`, a.at, a.len);
      }
      const nt = this.peek;
      let sign = 1;
      if (nt.k === 'op' && nt.v === '-') {
        this.next();
        sign = -1;
      }
      const n = this.peek;
      if (n.k !== 'num') fail(`${info.key} settings are numbers (for example ${info.key}(${info.params.map((p) => p.default).join(', ')}))`, n.at, Math.max(1, n.len));
      this.next();
      const value = sign * Number(n.v);
      if (setting.name in values) fail(`${setting.name} is given twice`, a.at, a.len);
      if (setting.integer && !Number.isInteger(value)) fail(`${setting.name} must be a whole number`, n.at, n.len);
      if (value < setting.min || value > setting.max) fail(`${setting.name} must be between ${setting.min} and ${setting.max}`, n.at, n.len);
      values[setting.name] = value;
      void named;
      if (this.peek.k === 'comma') {
        this.next();
        continue;
      }
      if (this.kind() !== 'rp') fail('missing ")"', this.peek.at, 1);
    }
    const close = this.next();
    const params = Object.fromEntries(info.params.map((p) => [p.name, values[p.name] ?? p.default]));
    if (info.key === 'macd' && params.fast >= params.slow) fail('the fast length must be shorter than the slow length', t.at, close.at + 1 - t.at); // the registry's own rule
    return { t: 'num', e: { ind: info.key, params, line: info.lines[0] }, at: t.at, end: close.at + 1, callable: info.key };
  }

  private rolling(name: string, t: Tok): Val {
    this.next();
    this.next();
    const of = this.num(this.or());
    if (this.peek.k !== 'comma') fail(`${name} needs a value and a window, for example ${name}(close, 20)`, this.peek.at, 1);
    this.next();
    const w = this.peek;
    if (w.k !== 'num') fail(`window must be a whole number from 2 to ${MAX_WINDOW}`, w.at, Math.max(1, w.len));
    this.next();
    const window = Number(w.v);
    if (!Number.isInteger(window) || window < 2 || window > MAX_WINDOW) fail(`window must be a whole number from 2 to ${MAX_WINDOW}`, w.at, w.len);
    const close = this.peek;
    if (close.k !== 'rp') fail('missing ")"', close.at, 1);
    this.next();
    return { t: 'num', e: { rolling: ROLLING[name], of: of.e, window }, at: t.at, end: close.at + 1 };
  }

  private cross(name: string, t: Tok): Val {
    this.next();
    this.next();
    const l = this.num(this.or());
    if (this.peek.k !== 'comma') fail(`${name} needs two values, for example ${name}(ema(12), ema(26))`, this.peek.at, 1);
    this.next();
    const r = this.num(this.or());
    const close = this.peek;
    if (close.k !== 'rp') fail('missing ")"', close.at, 1);
    this.next();
    return { t: 'bool', c: { op: name === 'crossover' ? 'crosses_above' : 'crosses_below', left: l.e, right: r.e }, at: t.at, end: close.at + 1 };
  }
}

/** `a and b` where either side may already be an and: one flat list. */
function join(kind: 'all' | 'any', a: Cond, b: Cond, _at: number): Cond {
  const items = (c: Cond): Cond[] => (kind in c ? (c as Record<string, Cond[]>)[kind] : [c]);
  return { [kind]: [...items(a), ...items(b)] } as Cond;
}

function shifted(e: Expr, amount: number, v: Val): Expr {
  if ('const' in e) return fail('a plain number cannot be shifted', v.at, v.end - v.at);
  const total = (e.shift ?? 0) + amount;
  if (total > MAX_SHIFT) return fail(`a shift must be a whole number from 0 to ${MAX_SHIFT}`, v.at, v.end - v.at);
  return total > 0 ? { ...e, shift: total } : e;
}

/** Compile a formula to a tree, or say what is wrong and where. */
export function compile(text: string): Result {
  try {
    return { ok: true, value: new Parser(text, tokenize(text)).parse() };
  } catch (e) {
    if (e instanceof FormulaError) return { ok: false, error: e };
    throw e;
  }
}
