/** A search box's drop-down: as you type, the instruments that match (symbol, company name, ISIN, index), from `/api/search`.
 * Up and down move through the list, Enter takes the highlighted one, Escape closes it, a click takes one. The page decides what taking one means. */
import type { Command } from './command';

export interface Hit {
  s: string; // symbol
  n: string; // name
  k: string; // stock, etf, sme or index
}

export interface SuggestOptions {
  /** What to search for given the box's text; null for no suggestions (for example after a command word that takes a layout name). */
  query?: (value: string) => string | null;
  /** A hit was chosen; `value` is the box's text at that moment. */
  onPick: (hit: Hit, value: string) => void;
  /** Only these kinds (comma separated for the server), all when empty. */
  kinds?: string;
  /** Enter with nothing highlighted takes the best match when the text is not itself a symbol on the list (for boxes that only take a symbol). */
  enterTakesBest?: boolean;
  fetcher?: typeof fetch;
  delayMs?: number;
  limit?: number;
}

export interface Suggest {
  close(): void;
  destroy(): void;
}

export const DELAY_MS = 120;
export const LIMIT = 8;

/** Where the highlight goes on a key: down from the last one leaves the list (-1), up from the box goes to the last one. */
export function nextActive(active: number, count: number, key: 'ArrowDown' | 'ArrowUp'): number {
  if (count <= 0) return -1;
  if (key === 'ArrowDown') return active + 1 >= count ? -1 : active + 1;
  return active <= -1 ? count - 1 : active - 1;
}

const VERBS: Record<string, 'add' | 'remove' | 'research' | 'fund' | 'lab'> = { ADD: 'add', REMOVE: 'remove', RESEARCH: 'research', FUND: 'fund', LAB: 'lab' };
const VIEWS = ['CHART', 'RESEARCH', 'FUND', 'LAB'] as const;

export interface Context {
  verb: 'add' | 'remove' | 'research' | 'fund' | 'lab' | null;
  /** `SBIN chart`, `SBIN fund`...: the word after the name. */
  view: 'chart' | 'research' | 'fund' | 'lab' | null;
  /** `ADD TCS TO Banks`: the list named. */
  list: string | null;
  /** What is left to search for. */
  text: string;
}

/** Split what is typed in the command line into the command word, a trailing view word, a list name and the text to search for. Null when the line
 * takes a name of its own (layouts, watchlists) or has nothing to search for. */
export function commandContext(value: string): Context | null {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const head = words[0].toUpperCase();
  if (head === 'LAYOUT' || head === 'LIST') return null;
  let verb: Context['verb'] = null;
  if (VERBS[head] && words.length > 1) {
    verb = VERBS[head];
    words.shift();
  }
  let list: string | null = null;
  if (verb === 'add') {
    const to = words.findIndex((w) => w.toUpperCase() === 'TO');
    if (to >= 0) {
      list = words.slice(to + 1).join(' ') || null;
      words.length = to;
    }
  }
  let view: Context['view'] = null;
  const last = words[words.length - 1]?.toUpperCase();
  if (!verb && words.length > 1 && (VIEWS as readonly string[]).includes(last)) {
    view = last.toLowerCase() as Context['view'];
    words.pop();
  }
  const text = words.join(' ').trim();
  return text ? { verb, view, list, text } : null;
}

/** The command a chosen instrument stands for, given the words typed around it. */
export function commandFor(symbol: string, ctx: Context | null): Command {
  if (ctx?.verb === 'add') return { kind: 'add', symbol, ...(ctx.list ? { list: ctx.list } : {}) };
  if (ctx?.verb === 'remove') return { kind: 'remove', symbol };
  if (ctx?.verb) return { kind: ctx.verb, symbol };
  if (ctx?.view === 'chart') return { kind: 'chart', symbol };
  if (ctx?.view) return { kind: ctx.view, symbol };
  return { kind: 'symbol', symbol };
}

export async function searchInstruments(text: string, options: { kinds?: string; limit?: number; fetcher?: typeof fetch; signal?: AbortSignal } = {}): Promise<Hit[]> {
  const f = options.fetcher ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const url = `/api/search?q=${encodeURIComponent(text)}&limit=${options.limit ?? LIMIT}${options.kinds ? `&kinds=${encodeURIComponent(options.kinds)}` : ''}`;
  try {
    const r = await f(url, { signal: options.signal });
    if (!r.ok) return [];
    const body = (await r.json()) as { results?: Hit[] };
    return Array.isArray(body.results) ? body.results : [];
  } catch {
    return []; // the box simply shows nothing: typing never errors
  }
}

export function attachSuggest(input: HTMLInputElement, options: SuggestOptions): Suggest {
  const list = document.createElement('div');
  list.className = 'suggest';
  list.setAttribute('role', 'listbox');
  list.hidden = true;
  document.body.append(list);
  let hits: Hit[] = [];
  let active = -1;
  let seq = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;

  const close = (): void => {
    seq++;
    controller?.abort();
    if (timer) clearTimeout(timer);
    timer = null;
    hits = [];
    active = -1;
    list.hidden = true;
    list.replaceChildren();
  };

  /** Under the box when there is room, above it when there is not, and never wider or lower than the window. */
  const place = (): void => {
    const r = input.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const width = Math.min(Math.max(r.width, 300), vw - 16);
    const below = vh - r.bottom - 8;
    const above = r.top - 8;
    const up = below < 160 && above > below;
    list.style.minWidth = `${width}px`;
    list.style.maxWidth = `${vw - 16}px`;
    list.style.left = `${Math.max(8, Math.min(r.left, vw - width - 8))}px`;
    list.style.left = `${Math.max(8, Math.min(r.left, vw - list.offsetWidth - 8))}px`; // again with the width the names actually need
    list.style.maxHeight = `${Math.max(80, Math.min(340, up ? above : below))}px`;
    list.style.top = up ? 'auto' : `${r.bottom + 2}px`;
    list.style.bottom = up ? `${vh - r.top + 2}px` : 'auto';
  };

  const paint = (): void => {
    list.replaceChildren();
    hits.forEach((hit, i) => {
      const row = document.createElement('div');
      row.className = `sg-item${i === active ? ' on' : ''}`;
      row.setAttribute('role', 'option');
      row.dataset.symbol = hit.s;
      const sym = document.createElement('b');
      sym.className = 'sg-sym';
      sym.textContent = hit.s;
      const name = document.createElement('span');
      name.className = 'sg-name';
      name.textContent = hit.n === hit.s ? '' : hit.n;
      const kind = document.createElement('span');
      kind.className = 'sg-kind';
      kind.textContent = hit.k;
      row.append(sym, name, kind);
      row.addEventListener('mousedown', (e) => e.preventDefault()); // keeps the focus in the box
      row.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        choose(hit);
      });
      row.addEventListener('pointermove', () => {
        if (active !== i) {
          active = i;
          list.querySelectorAll('.sg-item').forEach((n, j) => n.classList.toggle('on', j === i));
        }
      });
      list.append(row);
    });
    if (!hits.length) return void (list.hidden = true);
    list.hidden = false;
    place();
  };

  const choose = (hit: Hit): void => {
    const value = input.value;
    close();
    options.onPick(hit, value);
  };

  const ask = (): void => {
    const text = options.query ? options.query(input.value) : input.value;
    if (!text || !text.trim()) return close();
    const mine = ++seq;
    controller?.abort();
    controller = new AbortController();
    void searchInstruments(text, { kinds: options.kinds, limit: options.limit, fetcher: options.fetcher, signal: controller.signal }).then((found) => {
      if (mine !== seq || document.activeElement !== input) return; // a newer keystroke, or the box lost focus meanwhile
      hits = found;
      active = -1;
      paint();
    });
  };

  const onInput = (): void => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(ask, options.delayMs ?? DELAY_MS);
  };

  const onKey = (e: KeyboardEvent): void => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!hits.length) return;
      e.preventDefault();
      active = nextActive(active, hits.length, e.key);
      paint();
      return;
    }
    if (e.key === 'Escape' && hits.length) {
      e.preventDefault();
      e.stopImmediatePropagation();
      close();
      return;
    }
    if (e.key === 'Enter') {
      const typed = input.value.trim().toUpperCase();
      const takeBest = options.enterTakesBest && active < 0 && hits.length > 0 && hits[0].s !== typed;
      const index = active >= 0 ? active : takeBest ? 0 : -1;
      if (index >= 0 && hits[index]) {
        e.preventDefault();
        e.stopImmediatePropagation(); // the box's own Enter handling must not also run
        choose(hits[index]);
      } else close();
    }
  };

  input.autocomplete = 'off';
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKey, true); // before the box's own handlers
  input.addEventListener('blur', close);
  window.addEventListener('resize', close);
  return {
    close,
    destroy() {
      close();
      input.removeEventListener('input', onInput);
      input.removeEventListener('keydown', onKey, true);
      input.removeEventListener('blur', close);
      window.removeEventListener('resize', close);
      list.remove();
    },
  };
}
