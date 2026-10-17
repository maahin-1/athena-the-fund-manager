import { describe, expect, it } from 'vitest';
import { commandContext, commandFor, nextActive, searchInstruments } from '../src/suggest';

describe('nextActive', () => {
  it('goes down the list, leaves it after the last, and comes up from the box to the last', () => {
    expect(nextActive(-1, 3, 'ArrowDown')).toBe(0);
    expect(nextActive(1, 3, 'ArrowDown')).toBe(2);
    expect(nextActive(2, 3, 'ArrowDown')).toBe(-1);
    expect(nextActive(-1, 3, 'ArrowUp')).toBe(2);
    expect(nextActive(0, 3, 'ArrowUp')).toBe(-1);
    expect(nextActive(0, 0, 'ArrowDown')).toBe(-1);
  });
});

describe('commandContext', () => {
  it('a bare word or several are all text to search for', () => {
    expect(commandContext('tata mo')).toEqual({ verb: null, view: null, list: null, text: 'tata mo' });
    expect(commandContext('  rvnl ')?.text).toBe('rvnl');
  });
  it('takes the command word off the front and a view word off the end', () => {
    expect(commandContext('add tata st')).toEqual({ verb: 'add', view: null, list: null, text: 'tata st' });
    expect(commandContext('fund rail vik')).toEqual({ verb: 'fund', view: null, list: null, text: 'rail vik' });
    expect(commandContext('tata steel chart')).toEqual({ verb: null, view: 'chart', list: null, text: 'tata steel' });
    expect(commandContext('remove tcs')?.verb).toBe('remove');
  });
  it('reads the list in ADD ... TO ...', () => {
    expect(commandContext('add hdfc to My Banks')).toEqual({ verb: 'add', view: null, list: 'My Banks', text: 'hdfc' });
    expect(commandContext('add hdfc to')).toEqual({ verb: 'add', view: null, list: null, text: 'hdfc' });
  });
  it('has nothing to search for after a command word alone, or where a layout or list name goes', () => {
    expect(commandContext('add')?.text).toBe('add'); // a lone word that happens to be a command word is still searched as text
    expect(commandContext('layout my')).toBeNull();
    expect(commandContext('list main')).toBeNull();
    expect(commandContext('   ')).toBeNull();
    expect(commandContext('chart')?.text).toBe('chart');
  });
});

describe('commandFor', () => {
  it('turns the chosen symbol into the command the words around it meant', () => {
    expect(commandFor('TMPV', null)).toEqual({ kind: 'symbol', symbol: 'TMPV' });
    expect(commandFor('TMPV', commandContext('tata mo'))).toEqual({ kind: 'symbol', symbol: 'TMPV' });
    expect(commandFor('TMPV', commandContext('add tata mo'))).toEqual({ kind: 'add', symbol: 'TMPV' });
    expect(commandFor('TMPV', commandContext('add tata mo to Cars'))).toEqual({ kind: 'add', symbol: 'TMPV', list: 'Cars' });
    expect(commandFor('TMPV', commandContext('remove tata mo'))).toEqual({ kind: 'remove', symbol: 'TMPV' });
    expect(commandFor('TMPV', commandContext('fund tata mo'))).toEqual({ kind: 'fund', symbol: 'TMPV' });
    expect(commandFor('TMPV', commandContext('tata mo chart'))).toEqual({ kind: 'chart', symbol: 'TMPV' });
    expect(commandFor('TMPV', commandContext('tata mo lab'))).toEqual({ kind: 'lab', symbol: 'TMPV' });
  });
});

describe('searchInstruments', () => {
  const reply = (body: unknown, status = 200) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
  it('asks the server with the text, the limit and the kinds, and returns the hits', async () => {
    let url = '';
    const f = (async (u: string) => {
      url = u;
      return new Response(JSON.stringify({ results: [{ s: 'RVNL', n: 'Rail Vikas Nigam Limited', k: 'stock' }] }));
    }) as unknown as typeof fetch;
    const hits = await searchInstruments('rail & vik', { fetcher: f, kinds: 'stock,etf', limit: 5 });
    expect(hits).toEqual([{ s: 'RVNL', n: 'Rail Vikas Nigam Limited', k: 'stock' }]);
    expect(url).toBe('/api/search?q=rail%20%26%20vik&limit=5&kinds=stock%2Cetf');
  });
  it('is empty, never an error, when the server fails or answers nonsense', async () => {
    expect(await searchInstruments('x', { fetcher: reply({}, 503) })).toEqual([]);
    expect(await searchInstruments('x', { fetcher: reply({ results: 'no' }) })).toEqual([]);
    expect(await searchInstruments('x', { fetcher: (async () => { throw new Error('down'); }) as unknown as typeof fetch })).toEqual([]);
  });
});
