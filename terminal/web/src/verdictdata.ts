import type { AnalysisData } from './panels/analysis';

export type Tone = 'up' | 'dn' | 'flat';

export interface Analyst {
  name: string;
  signal: string;
  tone: Tone;
  confidence: number;
  coverage: string;
  reasoning: string;
  missing: string[];
}

/** What to do with the verdict: BUY, SELL, HOLD, or REVIEW when it should not be acted on without a person looking. */
export interface Decision {
  type: string;
  tone: Tone;
  reason: string;
}

/** One side of the debate: the case, the facts it relies on and its own weakest point, or why it could not be made. */
export interface DebateSide {
  side: string;
  case: string;
  evidence: string[];
  weakest: string;
  failed: string | null;
}

/** The council's answer beside the blend: the judge's stance (held to one step from the blend), why, and the debate when the analysts conflicted. */
export interface Council {
  stance: string;
  tone: Tone;
  blendRating: string;
  conviction: number | null;
  why: string;
  reliedOn: string[];
  /** What each fact id on the evidence sheet stands for (name and figure); empty for a council stored before the figures were kept. */
  facts: Record<string, string>;
  wouldChange: string;
  lookAgainDays: number | null;
  limited: string | null;
  model: string;
  debate: DebateSide[];
  failed: string | null;
}

export interface VerdictFacts {
  decision: Decision | null;
  council: Council | null;
  band: string;
  tone: Tone;
  conviction: number | null;
  raw: number | null;
  calibration: string | null;
  trust: number | null;
  risks: string[];
  analysts: Analyst[];
  skipped: { name: string; reason: string }[];
  notes: string[];
  disagree: string[];
  madeAt: string;
  dataAsOf: string;
  newerDays: number;
}

const BAND: Record<string, Tone> = { 'Strong Buy': 'up', Buy: 'up', Overweight: 'up', Hold: 'flat', Neutral: 'flat', Underweight: 'dn', Sell: 'dn', 'Strong Sell': 'dn' };
const SIGNAL: Record<string, Tone> = { bullish: 'up', bearish: 'dn', neutral: 'flat' };

export const toneOfBand = (band: string): Tone => BAND[band] ?? 'flat';
export const toneOfSignal = (signal: string): Tone => SIGNAL[signal] ?? 'flat';

const words = (s: string) => s.replace(/_/g, ' ');
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : typeof v === 'object' && v ? Object.entries(v).map(([k, why]) => `${k}: ${String(why)}`) : []);
/** `conflict` maps each side of the argument to the analysts on it: {bullish: ['valuation'], bearish: ['moat_quality']}. */
function sides(v: unknown): string[] {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return strings(v);
  return Object.entries(v).map(([side, who]) => `${side}: ${strings(who).map(words).join(', ') || '—'}`);
}
/** `skipped` maps an analyst to why it did not run; a plain list of names is accepted too. */
function skippedList(v: unknown): { name: string; reason: string }[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string').map((name) => ({ name: words(name), reason: '' }));
  if (typeof v === 'object' && v !== null) return Object.entries(v).map(([name, reason]) => ({ name: words(name), reason: String(reason) }));
  return [];
}
const text = (v: unknown): string => (typeof v === 'string' ? v : '');
const DECISION: Record<string, Tone> = { BUY: 'up', SELL: 'dn', HOLD: 'flat', REVIEW: 'flat' };

function readDecision(v: unknown): Decision | null {
  if (typeof v !== 'object' || v === null) return null;
  const d = v as Record<string, unknown>;
  const type = text(d.type);
  return type ? { type, tone: DECISION[type] ?? 'flat', reason: text(d.reason) } : null;
}

function readSide(side: string, v: unknown): DebateSide | null {
  if (typeof v !== 'object' || v === null) return null;
  const d = v as Record<string, unknown>;
  return { side, case: text(d.case), evidence: strings(d.evidence), weakest: text(d.weakest_point), failed: typeof d.failed === 'string' ? d.failed : null };
}

/** A fact as a reader would say it: "roe latest 48.7%", the figure in the unit the packet gave (a fraction is shown as a percentage). */
export function factLabel(f: Record<string, unknown>): string {
  const name = text(f.name).replace(/_/g, ' ');
  const value = f.value;
  if (typeof value !== 'number') return value === null || value === undefined ? `${name} (no figure)` : `${name} ${String(value)}`;
  const unit = text(f.unit);
  const shown = unit === 'fraction' ? `${+(value * 100).toPrecision(4)}%` : `${+value.toPrecision(4)}${unit && unit !== 'ratio' ? ` ${unit}` : ''}`;
  return `${name} ${shown}`;
}

function readFacts(v: unknown): Record<string, string> {
  if (typeof v !== 'object' || v === null) return {};
  const out: Record<string, string> = {};
  for (const [id, f] of Object.entries(v as Record<string, unknown>)) if (typeof f === 'object' && f !== null) out[id] = factLabel(f as Record<string, unknown>);
  return out;
}

function readCouncil(v: unknown): Council | null {
  if (typeof v !== 'object' || v === null) return null;
  const c = v as Record<string, unknown>;
  const judge = (c.judge ?? {}) as Record<string, unknown>;
  const debate = (typeof c.debate === 'object' && c.debate !== null ? c.debate : {}) as Record<string, unknown>;
  const failed = typeof c.failed === 'string' ? c.failed : typeof judge.failed === 'string' ? judge.failed : null;
  const stance = text(judge.stance);
  if (!stance && !failed) return null;
  return {
    stance,
    tone: BAND[stance] ?? 'flat',
    blendRating: text(judge.blend_rating),
    conviction: numberOrNull(judge.conviction),
    why: text(judge.why),
    reliedOn: strings(judge.relied_on),
    facts: readFacts(c.facts),
    wouldChange: text(judge.would_change_my_mind),
    lookAgainDays: numberOrNull(judge.look_again_days),
    limited: typeof judge.limited === 'string' ? judge.limited : null,
    model: text(c.judge_model),
    debate: [readSide('bull', debate.bull), readSide('bear', debate.bear)].filter((s): s is DebateSide => s !== null),
    failed,
  };
}

const numberOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
}

/** The stored verdict as plain facts. `skipped` maps an analyst to the reason it did not run; a plain list is accepted too. */
export function readVerdict(d: AnalysisData): VerdictFacts | null {
  const v = d.verdict;
  if (!v) return null;
  const payload = v.payload as Record<string, unknown>;
  const inner = (payload.verdict ?? {}) as Record<string, unknown>;
  const band = typeof inner.verdict === 'string' ? inner.verdict : 'no view';
  const specialists = (payload.specialists ?? {}) as Record<string, Record<string, unknown>>;
  const analysts: Analyst[] = Object.entries(specialists).map(([name, s]) => {
    const signal = typeof s.signal === 'string' ? s.signal : 'neutral';
    return {
      name: words(name),
      signal,
      tone: SIGNAL[signal] ?? 'flat',
      confidence: numberOrNull(s.confidence) ?? 0,
      coverage: typeof s.data_coverage === 'string' ? s.data_coverage : '',
      reasoning: typeof s.reasoning === 'string' ? s.reasoning : '',
      missing: strings(s.missing).map(words),
    };
  });
  return {
    decision: readDecision(payload.decision),
    council: readCouncil(payload.council),
    band,
    tone: BAND[band] ?? 'flat',
    conviction: numberOrNull(inner.conviction),
    raw: numberOrNull(inner.raw_conviction),
    calibration: typeof inner.calibration === 'string' ? inner.calibration : null,
    trust: numberOrNull(inner.trust),
    risks: strings(inner.key_risks),
    analysts,
    skipped: skippedList(payload.skipped),
    notes: strings(payload.notes),
    disagree: sides(payload.conflict),
    madeAt: v.made_at,
    dataAsOf: v.data_as_of,
    newerDays: d.market_day ? Math.max(0, daysBetween(v.data_as_of, d.market_day)) : 0,
  };
}
