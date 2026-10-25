/** "Explain this chart": the request the page sends and the reply it reads. The figures are worked out by the server in code; a model only phrases them. Pure. */
import type { Bar } from '../bars';
import type { Drawing } from './drawings/model';

export const MAX_BARS = 600;
export const MAX_DRAWINGS = 12;

export interface ExplainRequest {
  symbol: string;
  interval: string;
  bars: { t: number; o: number; h: number; l: number; c: number; v: number }[];
  drawings: { tool: string; points: { t: number; p: number }[]; text?: string }[];
}

export interface ExplainFact {
  id: string;
  name: string;
  value: number | string;
  unit: string;
  window: string;
}

export interface Explanation {
  symbol: string;
  interval: string;
  facts: ExplainFact[];
  read: string | null;
  cited: string[];
  model: string | null;
  notes: string[];
}

/** The bars on show (the latest `MAX_BARS`) and the drawings that are not hidden, as the server wants them. */
export function buildRequest(symbol: string, interval: string, bars: readonly Bar[], drawings: readonly Drawing[]): ExplainRequest {
  return {
    symbol,
    interval,
    bars: bars.slice(-MAX_BARS).map((b) => ({ t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v })),
    drawings: drawings.filter((d) => !d.hidden).slice(0, MAX_DRAWINGS).map((d) => ({ tool: d.tool, points: d.points.map((a) => ({ t: a.t, p: a.p })), ...(d.text ? { text: d.text } : {}) })),
  };
}

/** A reply from the server as an `Explanation`, or null when it is not one. */
export function readExplanation(raw: unknown): Explanation | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.facts)) return null;
  const facts = r.facts.filter(
    (f): f is ExplainFact => typeof f === 'object' && f !== null && typeof (f as ExplainFact).id === 'string' && typeof (f as ExplainFact).name === 'string' && ['number', 'string'].includes(typeof (f as ExplainFact).value),
  );
  const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
  return {
    symbol: typeof r.symbol === 'string' ? r.symbol : '',
    interval: typeof r.interval === 'string' ? r.interval : '',
    facts,
    read: typeof r.read === 'string' && r.read.trim() ? r.read.trim() : null,
    cited: strings(r.cited),
    model: typeof r.model === 'string' ? r.model : null,
    notes: strings(r.notes),
  };
}

/** The reading split into plain text and the fact ids it cites ([F3]), for the page to turn each id into a link to its line on the sheet. */
export function splitCitations(read: string): { text: string; id?: string }[] {
  const out: { text: string; id?: string }[] = [];
  let last = 0;
  for (const m of read.matchAll(/\[(F\d+)\]/g)) {
    if (m.index! > last) out.push({ text: read.slice(last, m.index) });
    out.push({ text: m[0], id: m[1] });
    last = m.index! + m[0].length;
  }
  if (last < read.length) out.push({ text: read.slice(last) });
  return out;
}

/** One fact as a line: name, value and unit, with the window when it has one. */
export function factLine(f: ExplainFact): string {
  const value = typeof f.value === 'number' ? (Number.isInteger(f.value) ? f.value.toLocaleString('en-IN') : f.value.toLocaleString('en-IN', { maximumFractionDigits: 3 })) : f.value;
  return `${f.name}: ${value}${f.unit && f.unit !== 'count' ? ` ${f.unit === 'percent' ? '%' : f.unit}` : ''}${f.window ? ` (${f.window})` : ''}`;
}

/** What to tell the person when the server refuses or fails. */
export function explainProblem(status: number): string {
  if (status === 429) return 'Another explanation is still running; try again in a moment.';
  if (status === 400) return 'The chart could not be read: too much data or none. Try a shorter range.';
  return 'The explanation is not available right now (the server could not run it).';
}
