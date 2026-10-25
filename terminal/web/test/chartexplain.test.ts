import { describe, expect, it } from 'vitest';
import type { Bar } from '../src/bars';
import { newDrawing } from '../src/chartlab/drawings/model';
import { buildRequest, explainProblem, factLine, MAX_BARS, MAX_DRAWINGS, readExplanation, splitCitations } from '../src/chartlab/explain';

const bars = (n: number): Bar[] => Array.from({ length: n }, (_, i) => ({ t: 1_700_000_000 + i * 86_400, o: 100 + i, h: 101 + i, l: 99 + i, c: 100.5 + i, v: 1000 + i }));

describe('the chart explanation request', () => {
  it('sends the latest bars on show and the drawings that are not hidden, with nothing the server does not use', () => {
    const line = newDrawing('trend', [{ t: 1, p: 100 }, { t: 2, p: 110 }]);
    const hidden = { ...newDrawing('hline', [{ t: 1, p: 105 }], {}, 'resistance'), hidden: true };
    const label = newDrawing('hline', [{ t: 1, p: 120 }], {}, 'target zone');
    const req = buildRequest('SBIN', '1D', bars(MAX_BARS + 50), [line, hidden, label]);
    expect(req.symbol).toBe('SBIN');
    expect(req.interval).toBe('1D');
    expect(req.bars).toHaveLength(MAX_BARS);
    expect(req.bars[req.bars.length - 1].c).toBe(100.5 + MAX_BARS + 49);
    expect(Object.keys(req.bars[0]).sort()).toEqual(['c', 'h', 'l', 'o', 't', 'v']);
    expect(req.drawings.map((d) => d.tool)).toEqual(['trend', 'hline']);
    expect(req.drawings[1]).toEqual({ tool: 'hline', points: [{ t: 1, p: 120 }], text: 'target zone' });
    expect(req.drawings[0]).not.toHaveProperty('text');
    const many = Array.from({ length: MAX_DRAWINGS + 5 }, () => newDrawing('hline', [{ t: 1, p: 1 }]));
    expect(buildRequest('X', '1D', bars(30), many).drawings).toHaveLength(MAX_DRAWINGS);
  });
});

describe('the reply', () => {
  const doc = {
    symbol: 'SBIN', interval: '1D', facts: [{ id: 'F1', name: 'bars shown', value: 60, unit: 'count', window: '1D bars' }, { id: 'F2', name: 'last close', value: 159.5, unit: 'rupees', window: '' }, { nonsense: true }],
    read: ' The last close is 159.5 [F2]. ', cited: ['F2', 7], model: 'a model', notes: ['n', 3],
  };

  it('reads an explanation and leaves out what is not one', () => {
    const got = readExplanation(doc)!;
    expect(got.facts.map((f) => f.id)).toEqual(['F1', 'F2']);
    expect(got.read).toBe('The last close is 159.5 [F2].');
    expect(got.cited).toEqual(['F2']);
    expect(got.notes).toEqual(['n']);
    expect(readExplanation({ ...doc, read: '   ' })!.read).toBeNull();
    expect(readExplanation({ ...doc, read: null, model: null })!.model).toBeNull();
    expect(readExplanation(null)).toBeNull();
    expect(readExplanation({ facts: 'no' })).toBeNull();
  });

  it('splits a reading into plain text and the fact ids it cites', () => {
    expect(splitCitations('Up 5% [F3] and near the high [F4][F5].')).toEqual([
      { text: 'Up 5% ' }, { text: '[F3]', id: 'F3' }, { text: ' and near the high ' }, { text: '[F4]', id: 'F4' }, { text: '[F5]', id: 'F5' }, { text: '.' },
    ]);
    expect(splitCitations('No citations.')).toEqual([{ text: 'No citations.' }]);
    expect(splitCitations('')).toEqual([]);
  });

  it('writes a fact as a line with its unit and window', () => {
    expect(factLine({ id: 'F1', name: 'bars shown', value: 60, unit: 'count', window: '1D bars' })).toBe('bars shown: 60 (1D bars)');
    expect(factLine({ id: 'F2', name: 'return', value: -3.4567891, unit: 'percent', window: '' })).toBe('return: -3.457 %');
    expect(factLine({ id: 'F3', name: 'last close', value: 12345.5, unit: 'rupees', window: '2026-10-23' })).toBe('last close: 12,345.5 rupees (2026-10-23)');
    expect(factLine({ id: 'F4', name: 'trend', value: 'aligned up', unit: '', window: '' })).toBe('trend: aligned up');
  });

  it('says what went wrong in words for the person', () => {
    expect(explainProblem(429)).toContain('still running');
    expect(explainProblem(400)).toContain('shorter range');
    expect(explainProblem(502)).toContain('not available');
  });
});
