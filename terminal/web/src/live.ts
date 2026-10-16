import { pct } from './format';
import { el } from './panels/base';
import { toneOfBand, toneOfSignal } from './verdictdata';

export interface LiveSpecialist {
  name: string;
  signal: string;
  confidence: number;
  coverage: string;
  reasoning: string;
  missing: string[];
}

export interface LivePanel {
  title: string;
  as_of: string;
  source: string;
  coverage: string;
  rows: { name: string; value: string | number; unit: string; window: string; note: string }[];
  missing: Record<string, string>;
}

/** The dashboard view the live analysis returns (the same one Streamlit's Analyse tab drew). */
export interface LiveView {
  status: string;
  query: string;
  identifier: string;
  name: string;
  asset_class: string;
  match: string;
  verdict: { verdict?: string; conviction?: number; key_risks?: string[]; resolution_path?: string; pre_overlay_verdict?: string; held_back_by?: string } | null;
  specialists: LiveSpecialist[];
  skipped: Record<string, string>;
  panels: LivePanel[];
  candidates: { identifier: string; name: string; asset_class: string; score: number }[];
  notes: string[];
}

export type LiveResult = { ok: true; view: LiveView } | { ok: false; error: string };

/** Ask the server for a live analysis. It calls the language model and can take two minutes; the first request also starts the analysis service. */
export async function analyseLive(query: string, relatives: boolean): Promise<LiveResult> {
  try {
    const r = await fetch('/api/analyse', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, relatives }) });
    if (r.status === 400) return { ok: false, error: 'That is not a ticker, ISIN or company name.' };
    if (!r.ok) {
      const body = (await r.json().catch(() => ({}))) as { error?: string };
      return { ok: false, error: body.error ?? 'The analysis could not be made.' };
    }
    return { ok: true, view: (await r.json()) as LiveView };
  } catch {
    return { ok: false, error: 'Could not reach the server.' };
  }
}

const words = (s: string) => s.replace(/_/g, ' ');

function metricValue(row: LivePanel['rows'][number]): string {
  const v = row.value;
  if (typeof v === 'string') return words(v);
  if (!Number.isFinite(v)) return '—';
  if (row.unit === 'fraction') return pct(v * 100);
  const text = Math.abs(v) >= 1000 ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : String(+v.toPrecision(5));
  return row.unit && row.unit !== 'count' && row.unit !== 'index' && row.unit !== 'label' ? `${text} ${row.unit}` : text;
}

function panelCard(p: LivePanel): HTMLElement {
  const card = el('section', 'rcard');
  card.append(el('h3', '', p.title), el('div', 'note', `as of ${p.as_of} · ${p.source} · coverage ${p.coverage}`));
  const grid = el('div', 'facts');
  for (const row of p.rows) {
    const k = el('span', 'k', words(row.name));
    if (row.window || row.note) k.title = [row.window, row.note].filter(Boolean).join(' · ');
    grid.append(k, el('span', 'v', metricValue(row)));
  }
  card.append(grid);
  const missing = Object.entries(p.missing);
  if (missing.length) card.append(el('div', 'note', `not available: ${missing.map(([k, why]) => `${words(k)} (${why})`).join('; ')}`));
  return card;
}

/** The analysis as a block for the Research page: the verdict, every specialist in full, the metric panels, risks and notes. `pick` handles a click on a candidate when the name was ambiguous. */
export function renderLive(view: LiveView, pick: (identifier: string) => void): HTMLElement {
  const box = el('section', 'rcard wide live');
  box.dataset.section = 'Live';
  box.append(el('h3', '', 'Live analysis'));
  if (view.status === 'needs_clarification') {
    box.append(el('div', 'note', `"${view.query}" could be more than one instrument. Pick one:`));
    const list = el('div', 'cands');
    for (const c of view.candidates) {
      const b = el('button', 'btn', `${c.identifier} · ${c.name} (${c.asset_class})`);
      b.addEventListener('click', () => pick(c.identifier));
      list.append(b);
    }
    box.append(list);
    return box;
  }
  const v = view.verdict;
  box.append(el('div', 'rname', `${view.identifier} · ${view.name} · ${view.asset_class} · matched by ${view.match.trim()}`));
  const head = el('div', 'vhead');
  head.append(el('span', `chip big ${toneOfBand(v?.verdict ?? '')}`, v?.verdict ?? 'no view'), el('span', 'vconv', `conviction ${v?.conviction ?? '—'}`));
  box.append(head);
  if (v?.pre_overlay_verdict) {
    box.append(el('div', 'flagline', `Held back from ${v.pre_overlay_verdict} by ${v.held_back_by === 'price data' ? 'unreliable price data' : 'your risk limits'}: the specialists said ${v.pre_overlay_verdict}.`));
  }
  if (view.status === 'no_view') box.append(el('div', 'flagline', 'No specialist had enough data to form a view.'));
  for (const r of v?.key_risks ?? []) box.append(el('div', 'flagline', `⚠ ${r}`));

  const grid = el('div', 'agrid');
  for (const s of view.specialists) {
    const tone = toneOfSignal(s.signal);
    const card = el('article', 'acard');
    card.dataset.analyst = words(s.name);
    const top = el('div', 'atop');
    top.append(el('span', 'an', words(s.name)), el('span', `chip ${tone}`, s.signal));
    const meter = el('div', 'meter');
    const fill = el('div', `fill ${tone}`);
    fill.style.width = `${Math.max(0, Math.min(100, s.confidence))}%`;
    meter.append(fill);
    card.append(top, meter, el('div', 'note', `confidence ${s.confidence}% · data ${s.coverage}`), el('div', 'reason', s.reasoning || 'no reasoning'));
    if (s.missing.length) card.append(el('div', 'note', `missing: ${s.missing.map(words).join(', ')}`));
    grid.append(card);
  }
  box.append(grid);
  for (const [name, why] of Object.entries(view.skipped)) box.append(el('div', 'note', `${words(name)} not run: ${why}`));

  const panels = el('div', 'agrid');
  for (const p of view.panels) panels.append(panelCard(p));
  if (view.panels.length) box.append(panels);
  for (const n of view.notes) box.append(el('div', 'note', `Note: ${n}`));
  return box;
}
