import { el } from './panels/base';
import type { Council, DebateSide, VerdictFacts } from './verdictdata';

const FACT_ID = /\b([TFN]\d{1,3})\b/g;

/** Text with each fact id on the evidence sheet shown as the id with its figure on hover (and a note of what it is when the council kept the figures). */
function cited(text: string, facts: Record<string, string>): (string | HTMLElement)[] {
  const out: (string | HTMLElement)[] = [];
  let at = 0;
  for (const m of text.matchAll(FACT_ID)) {
    const label = facts[m[1]];
    if (!label) continue;
    out.push(text.slice(at, m.index));
    const tag = el('abbr', 'fact', m[1]);
    tag.title = label;
    out.push(tag);
    at = (m.index ?? 0) + m[0].length;
  }
  out.push(text.slice(at));
  return out;
}

const side = (s: DebateSide, facts: Record<string, string> = {}): HTMLElement => {
  const box = el('div', 'debate-side');
  box.append(el('div', 'an', s.side === 'bull' ? 'Bull case' : 'Bear case'));
  if (s.failed) return (box.append(el('div', 'note', `could not be made: ${s.failed}`)), box);
  const why = el('div', 'why');
  why.append(...cited(s.case, facts));
  box.append(why);
  if (s.evidence.length) box.append(el('div', 'note', `relies on ${s.evidence.map((id) => facts[id] ?? id).join('; ')}`));
  if (s.weakest) box.append(el('div', 'note', `its own weakest point: ${s.weakest}`));
  return box;
};

function councilBlock(c: Council): HTMLElement {
  const box = el('div', 'council');
  if (c.failed) return (box.append(el('div', 'flagline', `The council could not give an answer (${c.failed}); the blend above stands.`)), box);
  const head = el('div', 'vhead');
  head.append(el('span', `chip ${c.tone}`, c.stance), el('span', 'vconv', `council${c.model ? ` (${c.model})` : ''}${c.conviction !== null ? ` · conviction ${c.conviction}` : ''}`));
  box.append(head);
  if (c.blendRating && c.blendRating !== c.stance) box.append(el('div', 'note', `The blend said ${c.blendRating}.`));
  if (c.limited) box.append(el('div', 'flagline', c.limited));
  if (c.why) {
    const why = el('div', 'why');
    why.append(...cited(c.why, c.facts));
    box.append(why);
  }
  if (c.reliedOn.length) {
    const known = c.reliedOn.filter((id) => c.facts[id]);
    const note = known.length ? `relied on ${known.map((id) => c.facts[id]).join('; ')}${known.length < c.reliedOn.length ? ` and ${c.reliedOn.length - known.length} more` : ''}` : `relied on ${c.reliedOn.join(', ')} (fact ids on the evidence sheet)`;
    box.append(el('div', 'note', note));
  }
  if (c.wouldChange) box.append(el('div', 'note', `would change its mind: ${c.wouldChange}`));
  if (c.lookAgainDays !== null) box.append(el('div', 'note', `look again in ${c.lookAgainDays} days`));
  if (c.debate.length) {
    const more = el('details', 'debate');
    more.append(el('summary', '', 'The debate (the analysts disagreed)'));
    for (const s of c.debate) more.append(side(s, c.facts));
    box.append(more);
  }
  box.append(el('div', 'note', 'The council is tested beside the blend and does not replace it; the blend is the verdict until the tests say otherwise.'));
  return box;
}

/** The decision (what to do with the verdict) and the council's answer, as elements to append under the analysts. Empty when the verdict has neither. */
export function decisionAndCouncil(v: VerdictFacts): HTMLElement[] {
  const out: HTMLElement[] = [];
  if (v.decision) {
    const row = el('div', 'vhead');
    row.append(el('span', `chip ${v.decision.tone}`, v.decision.type), el('span', 'vconv', v.decision.reason));
    out.push(row);
  }
  if (v.council) out.push(councilBlock(v.council));
  return out;
}
