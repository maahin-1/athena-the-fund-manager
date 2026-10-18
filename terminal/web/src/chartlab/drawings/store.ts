import type { KeyValue } from '../store';
import { serializeDoc, validateDoc, type Drawing } from './model';

export const SAVE_DELAY_MS = 400;
const SYMBOL = /^[A-Z0-9&^ -]{1,24}$/;
const KEY = 'athena.drawings.';

/** The upper-cased symbol drawings are kept under, or null for one the page does not draw on. */
export function symbolKey(symbol: string): string | null {
  const s = symbol.trim().toUpperCase();
  return SYMBOL.test(s) ? s : null;
}

/** What the store needs from the server: the saved text for a symbol (null when there is none) and a way to save it. Both can fail by throwing or returning false. */
export interface DrawingIO {
  get(symbol: string): Promise<string | null>;
  put(symbol: string, text: string): Promise<boolean>;
}

export const serverDrawingIO = (fetcher: typeof fetch = (...a) => fetch(...a)): DrawingIO => ({
  async get(symbol) {
    const r = await fetcher(`/api/drawings/${encodeURIComponent(symbol)}`);
    if (r.status === 204) return null;
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.text();
  },
  async put(symbol, text) {
    const r = await fetcher(`/api/drawings/${encodeURIComponent(symbol)}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: text });
    return r.ok;
  },
});

/** Drawings per symbol: kept in the browser at once and on the server 400 ms after the last change. A server that is down leaves the browser's copy in use and is tried again at
 *  the next save. A saved document that does not validate is ignored (the other copy, or nothing, is used). */
export class DrawingStore {
  private pending = new Map<string, Drawing[]>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private unsent = new Set<string>();

  constructor(
    private readonly io: DrawingIO,
    private readonly storage: KeyValue | null,
  ) {}

  private readLocal(key: string): Drawing[] | null {
    try {
      const text = this.storage?.getItem(KEY + key);
      return text ? validateDoc(JSON.parse(text), key).drawings : null;
    } catch {
      return null;
    }
  }

  private writeLocal(key: string, list: Drawing[]): void {
    try {
      this.storage?.setItem(KEY + key, serializeDoc(key, list));
    } catch {
      /* the server copy still holds them */
    }
  }

  /** The drawings for a symbol: the server's, else the browser's (which is then sent to the server). */
  async load(symbol: string): Promise<Drawing[]> {
    const key = symbolKey(symbol);
    if (!key) return [];
    if (this.pending.has(key)) return this.pending.get(key) as Drawing[]; // a change not yet sent is newer than either copy
    const local = this.readLocal(key);
    let text: string | null = null;
    let reached = true;
    try {
      text = await this.io.get(key);
    } catch {
      reached = false;
    }
    if (this.pending.has(key)) return this.pending.get(key) as Drawing[];
    if (text !== null) {
      try {
        const list = validateDoc(JSON.parse(text), key).drawings;
        this.writeLocal(key, list);
        return list;
      } catch {
        /* a bad server document: fall back to the browser's copy */
      }
    }
    if (local && local.length && reached) this.queue(key, local); // the server had nothing, the browser did: send it
    return local ?? [];
  }

  /** Keep these as the symbol's drawings. */
  save(symbol: string, list: Drawing[]): void {
    const key = symbolKey(symbol);
    if (!key) return;
    this.writeLocal(key, list);
    this.queue(key, list);
  }

  private queue(key: string, list: Drawing[]): void {
    this.pending.set(key, list);
    const old = this.timers.get(key);
    if (old) clearTimeout(old);
    this.timers.set(key, setTimeout(() => void this.send(key), SAVE_DELAY_MS));
  }

  private async send(key: string): Promise<void> {
    this.timers.delete(key);
    const list = this.pending.get(key);
    if (!list) return;
    let ok = false;
    try {
      ok = await this.io.put(key, serializeDoc(key, list));
    } catch {
      ok = false;
    }
    if (this.pending.get(key) === list) this.pending.delete(key); // nothing newer arrived while sending
    if (ok) this.unsent.delete(key);
    else this.unsent.add(key); // the browser's copy has it; the next save tries again
  }

  /** Send every change now. */
  async flush(): Promise<void> {
    for (const [key, timer] of [...this.timers]) {
      clearTimeout(timer);
      await this.send(key);
    }
  }

  /** Symbols whose last save did not reach the server. */
  get notSent(): string[] {
    return [...this.unsent];
  }
}

/** Undo and redo across the chart's own state and the drawings, in the order things were done. Each entry names which history the step belongs to. */
export type ActionKind = 'chart' | 'drawings';

export class ActionLog {
  private past: ActionKind[] = [];
  private future: ActionKind[] = [];

  push(kind: ActionKind): void {
    this.past.push(kind);
    if (this.past.length > 100) this.past.shift();
    this.future = [];
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  undo(): ActionKind | null {
    const k = this.past.pop();
    if (!k) return null;
    this.future.push(k);
    return k;
  }

  redo(): ActionKind | null {
    const k = this.future.pop();
    if (!k) return null;
    this.past.push(k);
    return k;
  }

  /** Forget the steps of one kind (its history was reset, as when the symbol changes). */
  drop(kind: ActionKind): void {
    this.past = this.past.filter((k) => k !== kind);
    this.future = this.future.filter((k) => k !== kind);
  }
}
