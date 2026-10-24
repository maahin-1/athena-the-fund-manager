import { el } from './panels/base';
import type { Council, DebateSide, VerdictFacts } from './verdictdata';

const side = (s: DebateSide): HTMLElement => {
  const box = el('div', 'debate-side');
  box.append(el('div', 'an', s.side === 'bull' ? 'Bull case' : 'Bear case'));
  if (s.failed) return (box.append(el('div', 'note', `could not be made: ${s.failed}`)), box);
  box.append(el('div', 'why', s.case));
  if (s.evidence.length) box.append(el('div', 'note', `relies on ${s.evidence.join(', ')}`));
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
  if (c.why) box.append(el('div', 'why', c.why));
  if (c.reliedOn.length) box.append(el('div', 'note', `relied on ${c.reliedOn.join(', ')} (fact ids on the evidence sheet)`));
  if (c.wouldChange) box.append(el('div', 'note', `would change its mind: ${c.wouldChange}`));
  if (c.lookAgainDays !== null) box.append(el('div', 'note', `look again in ${c.lookAgainDays} days`));
  if (c.debate.length) {
    const more = el('details', 'debate');
    more.append(el('summary', '', 'The debate (the analysts disagreed)'));
    for (const s of c.debate) more.append(side(s));
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
