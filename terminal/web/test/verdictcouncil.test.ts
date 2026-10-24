// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import type { AnalysisData } from '../src/panels/analysis';
import { readVerdict } from '../src/verdictdata';
import { decisionAndCouncil } from '../src/verdictview';

const data = (payload: Record<string, unknown>): AnalysisData => ({
  symbol: 'TCS', name: null, kind: 'stock', market_day: '2026-10-22', screen: null, technical: null, fundamentals: null,
  verdict: { made_at: '2026-10-24 12:06:00', data_as_of: '2026-10-22', close: 100, payload },
});
const base = { verdict: { verdict: 'Overweight', conviction: 10, raw_conviction: 30 }, specialists: {} };
const judge = {
  stance: 'Hold', asked_for: 'Hold', blend_rating: 'Overweight', conviction: 8, why: 'The price is above the model value (F45).', relied_on: ['F45', 'F15'],
  would_change_my_mind: 'An implied return above the required one (F47).', look_again_days: 20, limited: null,
};

describe('decision and council on a stored verdict', () => {
  it('reads the decision and the council beside the blend', () => {
    const v = readVerdict(data({ ...base, decision: { type: 'REVIEW', reason: 'analysts disagree' }, council: { judge_model: 'openai:gpt-5.4', debate: null, judge } }))!;
    expect(v.decision).toEqual({ type: 'REVIEW', tone: 'flat', reason: 'analysts disagree' });
    expect(v.council).toMatchObject({ stance: 'Hold', tone: 'flat', blendRating: 'Overweight', conviction: 8, reliedOn: ['F45', 'F15'], lookAgainDays: 20, model: 'openai:gpt-5.4', debate: [], failed: null });
  });

  it('an older verdict with neither is read without them', () => {
    const v = readVerdict(data(base))!;
    expect(v.decision).toBeNull();
    expect(v.council).toBeNull();
    expect(decisionAndCouncil(v)).toEqual([]);
  });

  it('draws the decision, the judge, what it relied on and what would change its mind', () => {
    const v = readVerdict(data({ ...base, decision: { type: 'BUY', reason: 'Overweight from 5 analysts' }, council: { judge_model: 'openai:gpt-5.4', debate: null, judge } }))!;
    const html = decisionAndCouncil(v).map((e) => e.outerHTML).join('');
    expect(html).toContain('BUY');
    expect(html).toContain('chip up');
    expect(html).toContain('The blend said Overweight.');
    expect(html).toContain('relied on F45, F15');
    expect(html).toContain('would change its mind: An implied return above the required one (F47).');
    expect(html).toContain('look again in 20 days');
    expect(html).toContain('does not replace it');
  });

  it('shows the debate, a side that failed, and a stance the code pulled back', () => {
    const debate = { bull: { case: 'Momentum is intact (T2).', evidence: ['T2'], weakest_point: 'Dear on P/E (F1).' }, bear: { failed: 'cited a figure not on the sheet' } };
    const v = readVerdict(data({ ...base, council: { judge_model: 'm', debate, judge: { ...judge, limited: 'the judge asked for Sell, more than one step from the blend; held to Underweight' } } }))!;
    const html = decisionAndCouncil(v).map((e) => e.outerHTML).join('');
    expect(html).toContain('Bull case');
    expect(html).toContain('Momentum is intact (T2).');
    expect(html).toContain('its own weakest point: Dear on P/E (F1).');
    expect(html).toContain('could not be made: cited a figure not on the sheet');
    expect(html).toContain('held to Underweight');
  });

  it('says so when the council fell over, and that the blend stands', () => {
    const v = readVerdict(data({ ...base, council: { failed: 'RuntimeError: provider down' } }))!;
    const html = decisionAndCouncil(v).map((e) => e.outerHTML).join('');
    expect(html).toContain('The council could not give an answer (RuntimeError: provider down); the blend above stands.');
    const judgeFailed = readVerdict(data({ ...base, council: { judge: { failed: 'no reply' }, debate: null } }))!;
    expect(judgeFailed.council?.failed).toBe('no reply');
  });
});
