import { addNote, editNote, NoteError, readDoc, removeNote, serializeDoc, type Note } from './model';

export interface NotebookIO {
  /** The saved notebook, or null when none has been saved. */
  load(): Promise<unknown | null>;
  save(body: string): Promise<void>;
}

export function serverNotebookIO(fetcher: typeof fetch = (...a) => fetch(...a)): NotebookIO {
  return {
    async load() {
      const r = await fetcher('/api/notebook');
      if (r.status === 204 || r.status === 404) return null;
      if (!r.ok) throw new Error(`notebook: HTTP ${r.status}`);
      return r.json();
    },
    async save(body) {
      const r = await fetcher('/api/notebook', { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
      if (!r.ok) throw new Error(`notebook: HTTP ${r.status}`);
    },
  };
}

/** The page's notebook: loaded once, changed in memory at once and saved to the server 400 ms after the last change. A server that cannot be reached leaves the notes
 *  already in memory and sets `problem`; a change made meanwhile is kept for the session and saved when the server comes back. */
export class Notebook {
  notes: Note[] = [];
  problem: string | null = null;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private loaded = false;

  constructor(private io: NotebookIO, private delay = 400) {}

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const fn of this.listeners) fn();
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      const raw = await this.io.load();
      if (raw !== null) this.notes = [...readDoc(raw).notes];
      this.loaded = true;
      this.problem = null;
    } catch (e) {
      this.problem = e instanceof NoteError ? 'The saved notebook could not be read; new notes start a fresh one.' : 'The notebook could not be loaded from the server; notes made now are kept for this session.';
    }
    this.emit();
  }

  private changed(): void {
    this.emit();
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.flush(), this.delay);
  }

  /** Save now. */
  async flush(): Promise<void> {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    try {
      await this.io.save(serializeDoc(this.notes));
      this.problem = null;
    } catch {
      this.problem = 'The notebook could not be saved to the server; it is kept for this session.';
    }
    this.emit();
  }

  add(symbol: string, text: string, tag = ''): Note {
    this.notes = addNote(this.notes, symbol, text, tag);
    this.changed();
    return this.notes[this.notes.length - 1];
  }

  edit(id: string, text: string, tag: string): void {
    this.notes = editNote(this.notes, id, text, tag);
    this.changed();
  }

  remove(id: string): void {
    this.notes = removeNote(this.notes, id);
    this.changed();
  }
}
