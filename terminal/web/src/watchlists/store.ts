import { activeList, addSymbol, isIndexSymbol, migrate, removeSymbol, symbolsOf, validate, type Doc } from './model';

/** Where the document lives on the server. `load` gives null when none is saved yet and throws when the server cannot be reached. */
export interface WatchlistIO {
  load(): Promise<unknown | null>;
  save(body: string): Promise<void>;
}

export interface KeyValue {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const LOCAL_KEY = 'athena.watchlists';
export const SAVE_DELAY_MS = 400;

export function serverIO(fetcher: typeof fetch = fetch): WatchlistIO {
  return {
    async load() {
      const r = await fetcher('/api/watchlists');
      if (r.status === 204 || r.status === 404) return null; // none saved yet
      if (!r.ok) throw new Error(`watchlists: HTTP ${r.status}`);
      return r.json();
    },
    async save(body) {
      const r = await fetcher('/api/watchlists', { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
      if (!r.ok) throw new Error(`watchlists: HTTP ${r.status}`);
    },
  };
}

/** The watchlists the page works with: the document, a copy in the browser, and a save to the server a moment after the last change.
 * `items`, `add`, `remove` and `subscribe` are the older single-list interface, now a view of the active list. */
export class WatchlistsStore {
  doc: Doc;
  /** A change that has not reached the server yet. */
  dirty = false;
  /** Nothing has been saved or changed yet, here or (as far as we know) on the server: a layout's own list may still seed it. */
  fresh: boolean;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private saving: Promise<void> = Promise.resolve();

  constructor(private io: WatchlistIO, private local: KeyValue | null, seed: readonly string[]) {
    const saved = this.fromLocal();
    this.fresh = saved === null;
    this.doc = saved ?? migrate(seed);
  }

  /** The first time only: a saved layout from before the watchlists were kept apart brings its symbols along. */
  seedFrom(symbols: readonly string[]): void {
    if (!this.fresh) return;
    this.fresh = false;
    this.doc = migrate(symbols);
    this.dirty = true;
    this.keepLocal();
    this.emit();
    this.schedule();
  }

  private fromLocal(): Doc | null {
    try {
      const text = this.local?.getItem(LOCAL_KEY);
      return text ? validate(JSON.parse(text)) : null;
    } catch {
      return null; // a damaged or blocked local copy is as good as none
    }
  }

  /** The active list's stocks and ETFs: what feeds, depth and statements can be asked for. Indices are left out (see `all`). */
  get items(): string[] {
    return symbolsOf(activeList(this.doc)).filter((s) => !isIndexSymbol(s));
  }

  /** Everything in the active list, indices included. */
  get all(): string[] {
    return symbolsOf(activeList(this.doc));
  }

  add(symbol: string): void {
    this.update((d) => addSymbol(d, symbol));
  }

  remove(symbol: string): void {
    this.update((d) => removeSymbol(d, symbol));
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Apply a change; nothing happens (and nothing is saved) when the function hands back the same document. */
  update(change: (doc: Doc) => Doc): void {
    const next = change(this.doc);
    if (next === this.doc) return;
    this.doc = next;
    this.fresh = false;
    this.dirty = true;
    this.keepLocal();
    this.emit();
    this.schedule();
  }

  /** Read the server's copy. It replaces ours unless we hold a change it has not seen; with none saved there, ours is sent. */
  async sync(): Promise<void> {
    let raw: unknown | null;
    try {
      raw = await this.io.load();
    } catch {
      return; // offline: the local copy stays and is saved when the server is back
    }
    if (raw === null) {
      this.dirty = true;
      this.schedule(0);
      return;
    }
    if (this.dirty) {
      this.schedule(0);
      return;
    }
    let remote: Doc;
    try {
      remote = validate(raw);
    } catch {
      return; // an unreadable server copy is never allowed to wipe the lists in use
    }
    this.fresh = false;
    if (JSON.stringify(remote) !== JSON.stringify(this.doc)) {
      this.doc = remote;
      this.keepLocal();
      this.emit();
    }
  }

  /** Save now (used by tests and before the page closes). Resolves when the attempt ends; a failure leaves `dirty` set for the next try. */
  flush(): Promise<void> {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.saving = this.saving.then(async () => {
      if (!this.dirty) return;
      const sent = this.doc;
      try {
        await this.io.save(JSON.stringify(sent));
        if (this.doc === sent) this.dirty = false;
      } catch {
        /* stays dirty; the next change or a sync tries again */
      }
    });
    return this.saving;
  }

  private schedule(delay = SAVE_DELAY_MS): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.flush(), delay);
  }

  private keepLocal(): void {
    try {
      this.local?.setItem(LOCAL_KEY, JSON.stringify(this.doc));
    } catch {
      /* storage may be full or blocked; the server copy is the real one */
    }
  }

  private emit(): void {
    this.listeners.forEach((fn) => fn());
  }
}
