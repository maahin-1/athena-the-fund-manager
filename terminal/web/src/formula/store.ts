import { customDefs, setCustom } from '../chartlab/registry';
import { defFor, type SavedFormula } from './defs';

/** The user's saved formulas: one document on the server (`/api/formulas`, as the watchlists and the screens are) with a copy in the browser. A formula that
 *  compiles is registered as an indicator the Chart Lab can draw; one that does not is kept as written, for the editor to show why. The browser copy is
 *  registered the moment the store is made, so a chart that uses a formula can be restored before the server has answered. */

export const MAX_FORMULAS = 50;
export const MAX_NAME = 40;
export const MAX_TEXT = 500;
export const LOCAL_KEY = 'athena.formulas';
const NAME = /^[A-Za-z0-9 _-]+$/;

export interface FormulasDoc {
  version: 1;
  formulas: SavedFormula[];
}

export class FormulasError extends Error {}

export interface FormulaIO {
  load(): Promise<unknown | null>;
  save(body: string): Promise<void>;
}

export interface KeyValue {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function validOne(raw: unknown): SavedFormula {
  if (!raw || typeof raw !== 'object') throw new FormulasError('a saved formula is not an object');
  const f = raw as Record<string, unknown>;
  const name = typeof f.name === 'string' ? f.name.trim() : '';
  if (name.length < 1 || name.length > MAX_NAME || !NAME.test(name)) throw new FormulasError(`a formula's name must be 1 to ${MAX_NAME} letters, digits, spaces, _ or -`);
  if (typeof f.text !== 'string') throw new FormulasError(`the formula "${name}" must be text`);
  if (f.text.length > MAX_TEXT) throw new FormulasError(`the formula "${name}" is at most ${MAX_TEXT} characters`);
  const pane = f.pane === undefined ? 'own' : f.pane;
  if (pane !== 'own' && pane !== 'overlay') throw new FormulasError(`the pane of "${name}" must be own or overlay`);
  return { name, text: f.text, pane };
}

export function validateFormulas(raw: unknown): FormulasDoc {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new FormulasError('the saved formulas are not a document');
  const d = raw as Record<string, unknown>;
  if (d.version !== 1) throw new FormulasError('the saved formulas have an unknown version');
  const list = Array.isArray(d.formulas) ? d.formulas : [];
  if (list.length > MAX_FORMULAS) throw new FormulasError(`at most ${MAX_FORMULAS} formulas can be saved`);
  const formulas = list.map(validOne);
  const seen = new Set<string>();
  for (const f of formulas) {
    const key = f.name.toLowerCase();
    if (seen.has(key)) throw new FormulasError(`the name "${f.name}" is used twice`);
    seen.add(key);
  }
  return { version: 1, formulas };
}

export function serverFormulaIO(fetcher: typeof fetch = fetch): FormulaIO {
  return {
    async load() {
      const r = await fetcher('/api/formulas');
      if (r.status === 204 || r.status === 404) return null;
      if (!r.ok) throw new Error(`formulas: HTTP ${r.status}`);
      return r.json();
    },
    async save(body) {
      const r = await fetcher('/api/formulas', { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
      if (!r.ok) throw new Error(`formulas: HTTP ${r.status}`);
    },
  };
}

export class FormulaStore {
  doc: FormulasDoc = { version: 1, formulas: [] };
  /** Why a formula that was saved does not compile now, by name. */
  errors: Record<string, string> = {};
  /** Set when the server could not be reached or its document could not be read. */
  problem: string | null = null;
  private listeners = new Set<() => void>();

  constructor(private io: FormulaIO, private local: KeyValue | null) {
    try {
      const text = this.local?.getItem(LOCAL_KEY);
      if (text) this.doc = validateFormulas(JSON.parse(text));
    } catch {
      /* a damaged or blocked browser copy is as good as none */
    }
    this.register();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  names(): string[] {
    return this.doc.formulas.map((f) => f.name);
  }

  get(name: string): SavedFormula | undefined {
    return this.doc.formulas.find((f) => f.name.toLowerCase() === name.toLowerCase());
  }

  /** Compile every saved formula and register the ones that compile as indicators; the rest go in `errors`. */
  private register(): void {
    const defs = [];
    this.errors = {};
    for (const f of this.doc.formulas) {
      const made = defFor(f);
      if ('def' in made) defs.push(made.def);
      else this.errors[f.name] = made.error.message;
    }
    setCustom(defs);
  }

  private keepLocal(): void {
    try {
      this.local?.setItem(LOCAL_KEY, JSON.stringify(this.doc));
    } catch {
      /* the browser copy is a convenience */
    }
  }

  private emit(): void {
    for (const fn of [...this.listeners]) fn();
  }

  async load(): Promise<void> {
    let raw: unknown;
    try {
      raw = await this.io.load();
    } catch {
      this.problem = 'Saved formulas could not be loaded: the server did not answer; the copy in this browser is used.';
      return this.emit();
    }
    if (raw !== null) {
      try {
        this.doc = validateFormulas(raw);
        this.problem = null;
        this.keepLocal();
      } catch (e) {
        this.problem = `Saved formulas could not be read (${e instanceof Error ? e.message : 'unknown'}).`;
      }
    }
    this.register();
    this.emit();
  }

  private async persist(): Promise<void> {
    this.register();
    this.keepLocal();
    this.emit();
    await this.io.save(JSON.stringify(this.doc));
  }

  /** Save a new formula or replace the one of that name (any case). It must compile; the error says why when it does not. */
  async save(formula: SavedFormula): Promise<void> {
    const clean = validOne(formula);
    const made = defFor(clean);
    if ('error' in made) throw new FormulasError(made.error.message);
    const at = this.doc.formulas.findIndex((f) => f.name.toLowerCase() === clean.name.toLowerCase());
    if (at < 0 && this.doc.formulas.length >= MAX_FORMULAS) throw new FormulasError(`at most ${MAX_FORMULAS} formulas can be saved`);
    const formulas = [...this.doc.formulas];
    if (at < 0) formulas.push(clean);
    else formulas[at] = clean;
    this.doc = { version: 1, formulas };
    await this.persist();
  }

  async remove(name: string): Promise<void> {
    this.doc = { version: 1, formulas: this.doc.formulas.filter((f) => f.name.toLowerCase() !== name.toLowerCase()) };
    await this.persist();
  }

  /** The registered indicators, for the menu. */
  defs() {
    return customDefs();
  }
}
