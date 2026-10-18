export type Command =
  | { kind: 'symbol'; symbol: string }
  | { kind: 'chart'; symbol: string }
  | { kind: 'layout'; name: string }
  | { kind: 'add'; symbol: string; list?: string } // to the active watchlist, or to the one named
  | { kind: 'list'; name: string } // switch watchlist
  | { kind: 'remove'; symbol: string }
  | { kind: 'research'; symbol: string | null } // the Research page, on a stock when one is given
  | { kind: 'fund'; symbol: string | null; section?: string } // the Fundamentals page, on a stock when one is given, scrolled to a section when named
  | { kind: 'lab'; symbol: string | null } // the Chart Lab page, on a stock when one is given
  | { kind: 'details'; symbol: string | null } // the Details panel of the Terminal, on a stock when one is given
  | { kind: 'terminal' }
  | { kind: 'screen' }
  | { kind: 'grid' }
  | { kind: 'error'; message: string };

export interface CommandContext {
  layouts: string[];
}

const SYMBOL = /^[A-Z0-9&-]{1,20}$/;
const INDEX_NAME = /^(NIFTY(?: [A-Z0-9]+){1,2}|INDIA VIX)$/;
const PRESETS = ['A', 'B', 'C', 'D', 'M'];

const fail = (message: string): Command => ({ kind: 'error', message });

function layoutNamed(word: string, ctx: CommandContext): string | null {
  const upper = word.toUpperCase();
  if (PRESETS.includes(upper)) return upper;
  return ctx.layouts.find((l) => l.toUpperCase() === upper) ?? null;
}

/** The command line. A bare word is a symbol, unless it is exactly a saved layout's name. */
export function parse(input: string, ctx: CommandContext): Command {
  const words = input.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return fail('type a symbol or a command');
  const head = words[0].toUpperCase();

  // an index has a name of several words: NIFTY 50, NIFTY BANK
  const index = /^(NIFTY(?: [A-Z0-9]+){1,2}?)(?: (CHART|RESEARCH|DETAILS))?$/.exec(words.join(' ').toUpperCase());
  if (index) return index[2] === 'CHART' ? { kind: 'chart', symbol: index[1] } : index[2] === 'RESEARCH' ? { kind: 'research', symbol: index[1] } : index[2] === 'DETAILS' ? { kind: 'details', symbol: index[1] } : { kind: 'symbol', symbol: index[1] };
  const detailsOfIndex = /^(?:DETAILS|DET) (NIFTY(?: [A-Z0-9]+){1,2}|INDIA VIX)$/.exec(words.join(' ').toUpperCase());
  if (detailsOfIndex) return { kind: 'details', symbol: detailsOfIndex[1] };

  if (head === 'ADD' || head === 'REMOVE') {
    const rest = words.slice(1);
    const to = head === 'ADD' ? rest.findIndex((w) => w.toUpperCase() === 'TO') : -1;
    const symbol = (to >= 0 ? rest.slice(0, to) : rest).join(' ').toUpperCase();
    const list = to >= 0 ? rest.slice(to + 1).join(' ') : '';
    if (!(SYMBOL.test(symbol) || INDEX_NAME.test(symbol))) return fail(`${head} needs one symbol or index, for example ${head} TCS${head === 'ADD' ? ' or ADD TCS TO My list' : ''}`);
    if (to >= 0 && !list) return fail('ADD ... TO needs a watchlist name');
    return head === 'ADD' ? { kind: 'add', symbol, ...(list ? { list } : {}) } : { kind: 'remove', symbol };
  }
  if (head === 'LIST') {
    const name = words.slice(1).join(' ');
    return name ? { kind: 'list', name } : fail('LIST needs a watchlist name, for example LIST Main');
  }
  if (head === 'LAYOUT') {
    const name = layoutNamed(words.slice(1).join(' '), ctx);
    return name ? { kind: 'layout', name } : fail(`no layout named "${words.slice(1).join(' ')}"`);
  }
  if (words.length === 1) {
    const saved = ctx.layouts.find((l) => l.toUpperCase() === words[0].toUpperCase());
    if (saved) return { kind: 'layout', name: saved };
    if (head === 'RESEARCH') return { kind: 'research', symbol: null };
    if (head === 'FUND') return { kind: 'fund', symbol: null };
    if (head === 'PEERS') return { kind: 'fund', symbol: null, section: 'Peers' };
    if (head === 'LAB') return { kind: 'lab', symbol: null };
    if (head === 'DETAILS' || head === 'DET') return { kind: 'details', symbol: null };
    if (head === 'TERMINAL') return { kind: 'terminal' };
    if (head === 'SCREEN') return { kind: 'screen' };
    if (head === 'GRID') return { kind: 'grid' };
    return SYMBOL.test(head) ? { kind: 'symbol', symbol: head } : fail(`"${words[0]}" is not a symbol`);
  }
  if (head === 'RESEARCH' && words.length === 2) {
    const symbol = words[1].toUpperCase();
    return SYMBOL.test(symbol) ? { kind: 'research', symbol } : fail(`"${words[1]}" is not a symbol`);
  }
  if ((head === 'DETAILS' || head === 'DET') && words.length === 2) {
    const symbol = words[1].toUpperCase();
    return SYMBOL.test(symbol) ? { kind: 'details', symbol } : fail(`"${words[1]}" is not a symbol`);
  }
  if (words.length === 2 && words[1].toUpperCase() === 'DETAILS') {
    return SYMBOL.test(head) ? { kind: 'details', symbol: head } : fail(`"${words[0]}" is not a symbol`);
  }
  if (head === 'LAB' && words.length === 2) {
    const symbol = words[1].toUpperCase();
    return SYMBOL.test(symbol) ? { kind: 'lab', symbol } : fail(`"${words[1]}" is not a symbol`);
  }
  if (words.length === 2 && words[1].toUpperCase() === 'LAB') {
    return SYMBOL.test(head) ? { kind: 'lab', symbol: head } : fail(`"${words[0]}" is not a symbol`);
  }
  if (head === 'PEERS' && words.length === 2) {
    const symbol = words[1].toUpperCase();
    return SYMBOL.test(symbol) ? { kind: 'fund', symbol, section: 'Peers' } : fail(`"${words[1]}" is not a symbol`);
  }
  if (words.length === 2 && words[1].toUpperCase() === 'PEERS') {
    return SYMBOL.test(head) ? { kind: 'fund', symbol: head, section: 'Peers' } : fail(`"${words[0]}" is not a symbol`);
  }
  if (head === 'FUND' && words.length === 2) {
    const symbol = words[1].toUpperCase();
    return SYMBOL.test(symbol) ? { kind: 'fund', symbol } : fail(`"${words[1]}" is not a symbol`);
  }
  if (words.length === 2 && words[1].toUpperCase() === 'FUND') {
    return SYMBOL.test(head) ? { kind: 'fund', symbol: head } : fail(`"${words[0]}" is not a symbol`);
  }
  if (words.length === 2 && words[1].toUpperCase() === 'RESEARCH') {
    return SYMBOL.test(head) ? { kind: 'research', symbol: head } : fail(`"${words[0]}" is not a symbol`);
  }
  if (words.length === 2 && words[1].toUpperCase() === 'CHART') {
    return SYMBOL.test(head) ? { kind: 'chart', symbol: head } : fail(`"${words[0]}" is not a symbol`);
  }
  return fail('unknown command');
}
