import { el } from '../panels/base';
import { fmtPlain } from './format';
import type { ValuationDoc } from './model';

const RUPEE = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const rupees = (v: number | null | undefined): string => (v === null || v === undefined || !Number.isFinite(v) ? '-' : `₹ ${RUPEE.format(v)}`);
const pct = (v: number | null | undefined, digits = 1): string => (v === null || v === undefined || !Number.isFinite(v) ? '-' : `${fmtPlain(v * 100, digits as 0 | 1 | 2)}%`);

/** Where the low, base and high values and the price sit on one scale, as percentages of the span (with a margin either side), for the range bar. */
export function barPositions(doc: Pick<ValuationDoc, 'low' | 'base' | 'high' | 'price'>): { low: number; base: number; high: number; price: number | null } | null {
  const { low, base, high, price } = doc;
  if (low === undefined || base === undefined || high === undefined || ![low, base, high].every(Number.isFinite)) return null;
  const points = [low, base, high, ...(price !== null && price !== undefined && Number.isFinite(price) ? [price] : [])];
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const at = (v: number): number => 5 + ((v - min) / span) * 90;
  return { low: at(low), base: at(base), high: at(high), price: price !== null && price !== undefined && Number.isFinite(price) ? at(price) : null };
}

/** The sentence that says what the market price is paying for. */
export function impliedLine(doc: ValuationDoc): string | null {
  if (doc.implied_growth === null || doc.implied_growth === undefined) return null;
  const parts = [`At the price of ${rupees(doc.price)} the market is paying for revenue growth of ${pct(doc.implied_growth)} a year for five years`];
  const against: string[] = [];
  if (doc.company_growth !== null && doc.company_growth !== undefined) against.push(`the company's last three years were ${pct(doc.company_growth)}`);
  if (doc.industry_growth !== null && doc.industry_growth !== undefined) against.push(`its industry is expected to grow ${pct(doc.industry_growth)}`);
  return parts[0] + (against.length ? `; ${against.join(' and ')}.` : '.');
}

/** The sentence that says what an owner earns at the price if the base case comes true, set against what the model asks for. */
export function returnLine(doc: ValuationDoc): string | null {
  if (doc.implied_return === null || doc.implied_return === undefined) return null;
  const need = doc.required_return !== null && doc.required_return !== undefined ? `; the model asks for ${pct(doc.required_return)}` : '';
  return `At ${rupees(doc.price)}, if the base-case cash flows come true, an owner earns about ${pct(doc.implied_return)} a year${need}.`;
}

function table(head: string[], rows: string[][]): HTMLElement {
  const wrap = el('div', 'ftable-wrap');
  const t = el('table', 'ftable');
  const thead = el('thead');
  const tr = el('tr');
  tr.append(...head.map((h) => el('th', '', h)));
  thead.append(tr);
  t.append(thead);
  const body = el('tbody');
  for (const r of rows) {
    const row = el('tr');
    r.forEach((text, i) => row.append(el('td', i === 0 ? 'flabel' : '', text)));
    body.append(row);
  }
  t.append(body);
  wrap.append(t);
  return wrap;
}

/** The Valuation section's content for one company. A company that cannot be valued gets the reasons, never a number. */
export function valuationBlock(doc: ValuationDoc): HTMLElement {
  const box = el('div', 'fvaluation');
  if (!doc.ok || doc.low === undefined || doc.base === undefined || doc.high === undefined) {
    box.append(el('div', 'note', `No value range for ${doc.symbol}: ${doc.missing.join('; ') || 'not enough data'}.`));
    for (const f of doc.flags ?? []) box.append(el('div', 'note', `Caution: ${f}.`));
    return box;
  }
  const lender = doc.model === 'excess_return';
  box.append(
    el('div', 'fval-head', `Value per share: ${rupees(doc.low)} to ${rupees(doc.high)}, base ${rupees(doc.base)}`),
    el('div', 'note', `${doc.price !== null ? `Price ${rupees(doc.price)} is ${fmtPlain(doc.price_to_base, 2)}x the base value. ` : ''}A range from three sets of stated assumptions, not a prediction${lender ? '; for a lender it is the equity value from the excess-return model' : ''}.`),
  );
  const pos = barPositions(doc);
  if (pos) {
    const bar = el('div', 'fval-bar');
    const span = el('div', 'fval-span');
    span.style.left = `${pos.low}%`;
    span.style.width = `${pos.high - pos.low}%`;
    const mark = (cls: string, at: number, title: string): HTMLElement => {
      const m = el('div', cls);
      m.style.left = `${at}%`;
      m.title = title;
      return m;
    };
    bar.append(span, mark('fval-base', pos.base, `base ${rupees(doc.base)}`));
    if (pos.price !== null) bar.append(mark('fval-price', pos.price, `price ${rupees(doc.price)}`));
    box.append(bar);
  }
  const implied = impliedLine(doc);
  if (implied) box.append(el('div', 'fval-implied', implied));
  const earned = returnLine(doc);
  if (earned) box.append(el('div', 'fval-implied', earned));
  if (doc.justified_price_to_book !== null && doc.justified_price_to_book !== undefined) {
    box.append(el('div', 'fval-implied', `The excess-return model puts the price to book at ${fmtPlain(doc.justified_price_to_book, 2)}x at the base case.`));
  }
  box.append(
    table(
      lender ? ['Case', 'Value per share', 'Return on equity', 'Stable return', 'Cost of equity'] : ['Case', 'Value per share', 'Revenue growth', 'Operating margin', 'Cost of capital'],
      doc.scenarios.map((s) =>
        lender
          ? [s.name, rupees(s.value), pct(s.roe), pct(s.stable_roe), pct(s.cost)]
          : [s.name, rupees(s.value), pct(s.growth), pct(s.margin), pct(s.cost)],
      ),
    ),
  );
  const c = doc.cost_of_capital;
  if (c && !lender) {
    box.append(
      el(
        'div',
        'note',
        `Cost of capital ${pct(c.inr.cost_of_capital)} (${pct(c.low)} to ${pct(c.high)}): cost of equity ${pct(c.inr.cost_of_equity)}, beta ${fmtPlain(c.levered_beta, 2)} (industry ${fmtPlain(c.unlevered_beta, 2)} unlevered), cost of debt ${pct(c.inr.pre_tax_cost_of_debt)} (${c.rating ?? 'unrated'}), rupee risk-free rate ${pct(c.riskfree_inr)}.`,
      ),
    );
  }
  if (doc.grid) {
    box.append(el('div', 'fg-title', 'Value per share: revenue growth (down) against operating margin (across)'));
    box.append(
      table(
        ['', ...doc.grid.margin.map((m) => pct(m))],
        doc.grid.values.map((row, i) => [pct(doc.grid!.growth[i]), ...row.map((v) => rupees(v))]),
      ),
    );
  }
  if (doc.sensitivity?.length) {
    const sens = el('div', 'fnotes');
    sens.append(el('div', 'fg-title', 'What the base value would be if (alternatives to look at, not the live inputs)'));
    for (const s of doc.sensitivity) sens.append(el('div', 'note', `${s.label}: ${rupees(s.base)} (${s.change >= 0 ? '+' : ''}${fmtPlain(s.change * 100, 0)}%)`));
    box.append(sens);
  }
  const notes = el('div', 'fnotes');
  for (const line of doc.assumptions ?? []) notes.append(el('div', 'note', line));
  for (const f of doc.flags ?? []) notes.append(el('div', 'note', `Caution: ${f}.`));
  notes.append(el('div', 'note', 'Computed in code from the stored filings and the dated reference tables (Damodaran Online, India); every input is listed above.'));
  box.append(notes);
  return box;
}
