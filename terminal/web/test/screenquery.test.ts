import { describe, expect, it } from 'vitest';
import { FIELDS, evaluate, limitsToQuery, parseQuery } from '../src/screenquery';
import type { Row } from '../src/screenlogic';

const row = (extra: Row = {}): Row => ({ symbol: 'ABC', name: 'ABC Bank Limited', kind: 'stock', sector: 'Financial Services', industry: 'Banks', indices: 'NIFTY 50|NIFTY BANK', pe: 18, pb: 2, roe: 0.2, market_cap_cr: 60000, debt_to_equity: 0.4, dividend_yield: 0.03, trend: 'aligned_up', ...extra });
const run = (query: string, r: Row = row()): boolean => {
  const parsed = parseQuery(query);
  if (!parsed.ok) throw new Error(`${query}: ${parsed.error.message}`);
  return evaluate(parsed.node, r);
};
const fails = (query: string) => {
  const parsed = parseQuery(query);
  if (parsed.ok) throw new Error(`${query} should not parse`);
  return parsed.error;
};

describe('comparisons', () => {
  it('compare numbers with every operator', () => {
    expect(run('pe < 20')).toBe(true);
    expect(run('pe <= 18')).toBe(true);
    expect(run('pe > 18')).toBe(false);
    expect(run('pe >= 18')).toBe(true);
    expect(run('pe = 18')).toBe(true);
    expect(run('pe == 18')).toBe(true);
    expect(run('pe != 18')).toBe(false);
    expect(run('pe <> 19')).toBe(true);
  });

  it('read a percentage field in percent, with or without the sign, and refuse a sign on any other field', () => {
    expect(run('roe > 15')).toBe(true);
    expect(run('roe > 15%')).toBe(true);
    expect(run('roe > 25%')).toBe(false);
    expect(run('dividend_yield >= 3')).toBe(true);
    expect(fails('pe > 5%').message).toMatch(/pe is not a percentage/);
  });

  it('know the short names, and any case', () => {
    expect(run('MCAP > 50000')).toBe(true);
    expect(run('yield > 2 and margin is null and de < 1 and growth is null')).toBe(true);
    expect(run('Market_Cap_Cr < 1000')).toBe(false);
  });

  it('between is inclusive and its and belongs to it', () => {
    expect(run('pe between 18 and 20')).toBe(true);
    expect(run('pe between 5 and 10')).toBe(false);
    expect(run('pe between 10 and 20 and roe > 15')).toBe(true);
    expect(run('roe between 10% and 15%')).toBe(false);
  });

  it('in lists numbers or text, and not in negates it', () => {
    expect(run('pe in (10, 18, 30)')).toBe(true);
    expect(run('sector in ("Technology", "financial services")')).toBe(true);
    expect(run('sector not in ("Technology", "Healthcare")')).toBe(true);
    expect(run('sector not in ("Financial Services")')).toBe(false);
  });
});

describe('text and sets', () => {
  it('match text without regard to case, quoted or a bare word', () => {
    expect(run('sector = "financial services"')).toBe(true);
    expect(run('trend = aligned_up')).toBe(true);
    expect(run('sector != "Technology"')).toBe(true);
    expect(run('industry = Banks')).toBe(true);
  });

  it('contains looks for a piece of text', () => {
    expect(run('name contains "bank"')).toBe(true);
    expect(run('name contains "steel"')).toBe(false);
    expect(fails('pe contains 1').message).toMatch(/contains is for text/);
  });

  it('index is a set: equal means is a member, not equal means is not', () => {
    expect(run('index = "nifty 50"')).toBe(true);
    expect(run('index = "NIFTY IT"')).toBe(false);
    expect(run('index != "NIFTY IT"')).toBe(true);
    expect(run('index in ("NIFTY IT", "nifty bank")')).toBe(true);
    expect(run('index = "NIFTY 50"', row({ indices: null }))).toBe(false);
    expect(run('index != "NIFTY 50"', row({ indices: null }))).toBe(true); // in no index at all
  });
});

describe('missing figures', () => {
  it('meet no limit, whichever way it points, and is null finds them', () => {
    const blank = row({ pe: null, sector: null });
    expect(run('pe < 100', blank)).toBe(false);
    expect(run('pe > 0', blank)).toBe(false);
    expect(run('pe != 5', blank)).toBe(false);
    expect(run('pe between 0 and 100', blank)).toBe(false);
    expect(run('sector = "x"', blank)).toBe(false);
    expect(run('pe is null', blank)).toBe(true);
    expect(run('pe is not null', blank)).toBe(false);
    expect(run('sector is null', blank)).toBe(true);
    expect(run('not (pe < 100)', blank)).toBe(true); // not turns "no figure" into a yes: write is not null to ask for a figure
  });
});

describe('and, or, not and brackets', () => {
  it('give not the tightest hold, then and, then or', () => {
    const q = 'pe < 5 or pe > 50 and roe > 20';
    expect(run(q, row({ pe: 3, roe: 0 }))).toBe(true);
    expect(run(q, row({ pe: 60, roe: 0.1 }))).toBe(false);
    expect(run(q, row({ pe: 60, roe: 0.3 }))).toBe(true);
    expect(run('not pe > 20 and roe > 15')).toBe(true);
    expect(run('not pe > 20 or roe > 50')).toBe(true);
  });

  it('let brackets change that, and nest', () => {
    expect(run('(pe < 5 or pe > 50) and roe > 20', row({ pe: 60, roe: 0.1 }))).toBe(false);
    expect(run('not (pe < 5 or (pe > 50 and roe > 20))')).toBe(true);
    expect(run('((pe < 20))')).toBe(true);
  });

  it('keywords are not case sensitive', () => {
    expect(run('PE < 20 AND ROE > 15 OR NOT PE > 1')).toBe(true);
  });
});

describe('an empty query and the field list', () => {
  it('matches everything', () => {
    expect(run('')).toBe(true);
    expect(run('   ')).toBe(true);
  });

  it('every field has names, a label, a kind and an example that parses', () => {
    expect(FIELDS.length).toBeGreaterThan(20);
    for (const f of FIELDS) {
      expect(f.names.length).toBeGreaterThan(0);
      expect(f.label).not.toBe('');
      expect(parseQuery(f.example).ok, `${f.names[0]}: ${f.example}`).toBe(true);
    }
    expect(new Set(FIELDS.flatMap((f) => f.names)).size).toBe(FIELDS.flatMap((f) => f.names).length); // no name belongs to two fields
  });
});

describe('errors say what and where', () => {
  it('name an unknown field and suggest the nearest', () => {
    const e = fails('pee < 5');
    expect(e.message).toMatch(/unknown field "pee"/);
    expect(e.message).toMatch(/did you mean "pe"/);
    expect([e.at, e.length]).toEqual([0, 3]);
    expect(fails('zzzzzz > 1').message).not.toMatch(/did you mean/);
  });

  it('point at the place a value is missing, or the text ends', () => {
    expect(fails('pe <').message).toMatch(/expected a number/);
    expect(fails('pe <').at).toBe(4);
    expect(fails('pe < 5 and').message).toMatch(/expected a condition/);
    expect(fails('pe').message).toMatch(/expected a comparison/);
  });

  it('catch brackets, quotes, stray words and bad numbers', () => {
    expect(fails('(pe < 5').message).toMatch(/missing "\)"/);
    expect(fails('pe < 5)').message).toMatch(/unexpected "\)"/);
    expect(fails('name = "abc').message).toMatch(/text is not closed/);
    expect(fails('pe < 5 roe > 2').message).toMatch(/unexpected "roe"; put and or or between conditions/);
    expect(fails('pe < 1.2.3').message).toMatch(/not a number/);
    expect(fails('pe < 5 # x').message).toMatch(/unexpected character "#"/);
    expect(fails('pe in 5').message).toMatch(/expected "\("/);
    expect(fails('pe in ()').message).toMatch(/expected a number or text/);
    expect(fails('pe < "x"').message).toMatch(/pe takes a number/);
    expect(fails('sector < 3').message).toMatch(/sector is text/);
  });
});

describe('limits as a query', () => {
  it('writes the preset ranges in the units the page shows, and parses back to the same rows', () => {
    const text = limitsToQuery({ pe: [0, 15], roe: [0.15, null], market_cap_cr: [null, 100] });
    expect(text).toBe('pe between 0 and 15 and roe >= 15 and market_cap_cr <= 100');
    expect(run(text, row({ pe: 10, roe: 0.2, market_cap_cr: 50 }))).toBe(true);
    expect(run(text, row({ pe: 10, roe: 0.1, market_cap_cr: 50 }))).toBe(false);
    expect(limitsToQuery({})).toBe('');
    expect(limitsToQuery({ pe: [null, null] })).toBe('');
  });
});
