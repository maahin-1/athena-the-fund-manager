import { describe, expect, it } from 'vitest';
import type { LabState } from '../src/chartlab/model';
import { deserialize, History, serialize, StateError, Templates, type KeyValue } from '../src/chartlab/store';

const state: LabState = {
  type: 'heikin', scale: 'log', adjusted: false, compare: 'NIFTY 50',
  indicators: [{ id: 'rsi', params: { length: 9 }, hidden: false, colors: {} }, { id: 'bb', params: { length: 20, mult: 2 }, hidden: true, colors: { upper: '#ff0000' } }],
};

class Memory implements KeyValue {
  data: Record<string, string> = {};
  getItem(k: string): string | null {
    return this.data[k] ?? null;
  }
  setItem(k: string, v: string): void {
    this.data[k] = v;
  }
}

describe('history', () => {
  it('undoes and redoes in order and drops the redo list on a new push', () => {
    const h = new History(0);
    h.push(1);
    h.push(2);
    h.push(3);
    expect([h.undo(), h.undo(), h.redo()]).toEqual([2, 1, 2]);
    expect([h.canUndo, h.canRedo]).toEqual([true, true]);
    h.push(9);
    expect(h.canRedo).toBe(false);
    expect(h.current).toBe(9);
    expect([h.undo(), h.undo(), h.undo()]).toEqual([2, 1, 0]);
    expect(h.undo()).toBeNull();
  });

  it('keeps at most 50 steps', () => {
    const h = new History(0);
    for (let i = 1; i <= 80; i++) h.push(i);
    let steps = 0;
    while (h.undo() !== null) steps++;
    expect(steps).toBe(50);
    expect(h.current).toBe(30);
  });

  it('reset clears both sides', () => {
    const h = new History(0);
    h.push(1);
    h.undo();
    h.reset(5);
    expect([h.current, h.canUndo, h.canRedo]).toEqual([5, false, false]);
  });
});

describe('state serialisation', () => {
  it('round-trips', () => {
    expect(deserialize(serialize(state))).toEqual(state);
  });

  it('refuses the wrong shapes with a reason', () => {
    const bad = (patch: object) => () => deserialize(JSON.stringify({ version: 1, ...state, ...patch }));
    expect(() => deserialize('not json')).toThrow(StateError);
    expect(() => deserialize('[]')).toThrow(/not a chart state/);
    expect(() => deserialize(JSON.stringify({ ...state }))).toThrow(/not a chart state/); // no version
    expect(bad({ type: 'cube' })).toThrow(/chart type/);
    expect(bad({ scale: 'x' })).toThrow(/scale/);
    expect(bad({ adjusted: 'yes' })).toThrow(/adjusted/);
    expect(bad({ compare: '../etc' })).toThrow(/compare/);
    expect(bad({ indicators: [{ id: 'nope', params: {} }] })).toThrow(/unknown indicator nope/);
    expect(bad({ indicators: [{ id: 'rsi', params: { length: { x: 1 } } }] })).toThrow(/settings/);
    expect(bad({ indicators: [{ id: 'rsi', params: {}, colors: { line: 'red' } }] })).toThrow(/colours/);
    expect(bad({ indicators: new Array(41).fill({ id: 'rsi', params: {} }) })).toThrow(/at most 40/);
  });
});

describe('templates', () => {
  it('save, list, load and remove, and survive a new session on the same storage', () => {
    const storage = new Memory();
    const t = new Templates(storage);
    t.save('Trend setup', state);
    t.save('Another', { ...state, type: 'candles' });
    expect(t.list()).toEqual(['Another', 'Trend setup']);
    expect(new Templates(storage).load('Trend setup')).toEqual(state);
    t.remove('Another');
    expect(new Templates(storage).list()).toEqual(['Trend setup']);
    expect(() => t.load('Another')).toThrow(/no template/);
  });

  it('refuses a bad name', () => {
    const t = new Templates(new Memory());
    for (const name of ['', 'x'.repeat(41), 'a/b', '<b>', 'semi;colon']) expect(() => t.save(name, state), name).toThrow(StateError);
    expect(Templates.nameOk('My-set_1')).toBe(true);
  });

  it('works from memory when storage is missing or throws, and ignores junk already in it', () => {
    const throwing: KeyValue = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
    for (const storage of [null, throwing]) {
      const t = new Templates(storage);
      t.save('Mine', state);
      expect(t.load('Mine')).toEqual(state);
    }
    const junk = new Memory();
    junk.setItem('athena.chartlab.templates', JSON.stringify({ 'bad/name': 'x', ok: 5, fine: serialize(state) }));
    expect(new Templates(junk).list()).toEqual(['fine']);
    junk.setItem('athena.chartlab.templates', '{not json');
    expect(new Templates(junk).list()).toEqual([]);
  });
});
