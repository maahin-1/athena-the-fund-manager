/** The Analyse button's documents (see `athena.forensic.job`): the job's status and the report of one draft offer document. Read from the server and never trusted for their shape. Pure. */
export const REPORT_SCHEMA = 'athena.forensic.report/1';

export interface Term {
  name: string;
  printed: string;
  page: number | null;
}

export interface Difference {
  id: string;
  group: string;
  title: string;
  reason: string;
  pages: number[];
  left: Term[];
  right: Term[];
}

export interface FlagRow {
  id: string;
  status: string;
  text: string;
  why: string;
  innocent: string[];
}

export interface GroupRow {
  id: string;
  title: string;
  held: number;
  differ: number;
  notRun: number;
}

export interface ReportDoc {
  generatedAt: string;
  company: string;
  kind: string;
  pages: number;
  sectionsFound: number;
  sectionsNotFound: string[];
  pagesWithoutText: number;
  concernLow: string;
  concernHigh: string;
  established: number;
  singleSource: number;
  flags: FlagRow[];
  held: number;
  differ: number;
  notRunCount: number;
  groups: GroupRow[];
  differences: Difference[];
  notRun: { reason: string; count: number }[];
  notes: string[];
  notBuilt: string[];
  notExamined: { lane: string; why: string }[];
  limits: string[];
  narrative: string;
  caveat: string;
  modelCalls: number;
}

export type JobStatus =
  | { state: 'idle' }
  | { state: 'running'; url: string; seconds: number }
  | { state: 'done'; url: string; report: ReportDoc }
  | { state: 'failed'; url: string; error: string };

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const int = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.trunc(v)) : 0);
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isObject) : []);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const term = (r: Record<string, unknown>): Term => ({ name: str(r.name), printed: str(r.printed), page: typeof r.page === 'number' && Number.isFinite(r.page) ? Math.trunc(r.page) : null });

/** The report of the job, or null when the document is not one of this schema, did not succeed, or lacks its core parts. */
export function readReport(raw: unknown): ReportDoc | null {
  if (!isObject(raw) || raw.schema !== REPORT_SCHEMA || raw.ok !== true || !isObject(raw.document) || !isObject(raw.concern) || !isObject(raw.checks) || !isObject(raw.checks.summary)) return null;
  const d = raw.document;
  const c = raw.concern;
  const k = raw.checks;
  const s = k.summary as Record<string, unknown>;
  return {
    generatedAt: str(raw.generated_at), company: str(d.company), kind: str(d.kind), pages: int(d.pages), sectionsFound: list(d.sections).length, sectionsNotFound: strings(d.sections_not_found), pagesWithoutText: int(d.pages_without_text),
    concernLow: str(c.low) || 'unknown', concernHigh: str(c.high) || 'unknown', established: int(c.established), singleSource: int(c.single_source),
    flags: list(raw.flags).map((f): FlagRow => ({ id: str(f.id), status: str(f.status), text: str(f.text), why: str(f.why), innocent: strings(f.innocent_explanations) })),
    held: int(s.held), differ: int(s.differ), notRunCount: int(s.not_run),
    groups: list(k.groups).map((g): GroupRow => ({ id: str(g.id), title: str(g.title), held: int(g.held), differ: int(g.differ), notRun: int(g.not_run) })),
    differences: list(k.differences).map((x): Difference => ({ id: str(x.id), group: str(x.group), title: str(x.title), reason: str(x.reason), pages: Array.isArray(x.pages) ? x.pages.filter((p): p is number => typeof p === 'number') : [], left: list(x.left).map(term), right: list(x.right).map(term) })),
    notRun: list(k.not_run).map((x) => ({ reason: str(x.reason), count: int(x.count) })),
    notes: strings(k.notes), notBuilt: strings(k.not_built),
    notExamined: list(raw.not_examined).map((x) => ({ lane: str(x.lane), why: str(x.why) })),
    limits: strings(raw.limits), narrative: str(raw.narrative), caveat: str(raw.caveat), modelCalls: isObject(raw.cost) ? int(raw.cost.model_calls) : 0,
  };
}

/** What `GET /api/forensic` said, or null when it is not a status. */
export function readJobStatus(raw: unknown): JobStatus | null {
  if (!isObject(raw)) return null;
  switch (raw.state) {
    case 'idle':
      return { state: 'idle' };
    case 'running':
      return { state: 'running', url: str(raw.url), seconds: int(raw.seconds) };
    case 'failed':
      return { state: 'failed', url: str(raw.url), error: str(raw.error) || 'the analysis failed' };
    case 'done': {
      const report = readReport(raw.report);
      return report ? { state: 'done', url: str(raw.url), report } : null;
    }
    default:
      return null;
  }
}

/** The concern line: a range, and how many differences stand behind it. Never a verdict. */
export function concernText(r: Pick<ReportDoc, 'concernLow' | 'concernHigh' | 'established' | 'singleSource'>): string {
  const level = r.concernLow === r.concernHigh ? r.concernLow : `${r.concernLow} to ${r.concernHigh}`;
  return `Concern level: ${level} (${r.established} established ${r.established === 1 ? 'difference' : 'differences'}, ${r.singleSource} with one source only)`;
}

export const runningText = (seconds: number): string => `Reading and checking the draft… ${seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`} so far (a long draft takes a few minutes)`;

/** What to tell the person when the server refuses to start a job. */
export function startProblem(status: number): string {
  if (status === 400 || status === 422) return 'This document cannot be analysed: only a PDF on the SEBI site is read.';
  if (status === 409) return 'Another document is being analysed; wait for it to finish.';
  return 'The analysis could not be started just now; try again in a minute.';
}

/** A company name or a kind as the server accepts it: letters, digits and a few marks; anything else becomes a space. */
export function plainName(value: string, fallback: string): string {
  const text = value.replace(/[^\p{L}\p{N} &.,\-()'/]/gu, ' ').replace(/\s+/g, ' ').trim().slice(0, 120);
  return text || fallback;
}

/** A term as a sentence fragment: its name, the figure as printed and the page. */
export const termText = (t: Term): string => `${t.name}: ${t.printed}${t.page ? ` (page ${t.page})` : ''}`;
