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

export interface VerdictFacts {
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
