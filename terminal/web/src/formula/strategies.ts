import { blankForm, type StrategyForm } from './strategy';

/** Saved strategies: one document on the server (`/api/strategies`), each a name and the form as it was typed (the text of the entry and the exit and the risk
 *  options), so that opening one puts the text back and the tree is built again. */

export const MAX_STRATEGIES = 50;
export const MAX_NAME = 60;
const MAX_FORMULA = 500;
const MAX_NUMBER = 12;

export interface SavedStrategy {
  name: string;
  form: StrategyForm;
}

export interface StrategiesDoc {
  version: 1;
  strategies: SavedStrategy[];
}

export class StrategiesError extends Error {}

function validOne(raw: unknown): SavedStrategy {
  if (!raw || typeof raw !== 'object') throw new StrategiesError('a saved strategy is not an object');
  const s = raw as Record<string, unknown>;
  const name = typeof s.name === 'string' ? s.name.trim() : '';
  if (name.length < 1 || name.length > MAX_NAME) throw new StrategiesError(`a strategy's name must be 1 to ${MAX_NAME} characters`);
  const source = s.form && typeof s.form === 'object' ? (s.form as Record<string, unknown>) : {};
  const form = blankForm();
  for (const field of Object.keys(form) as (keyof StrategyForm)[]) {
    const value = source[field];
    if (value === undefined) continue;
    if (typeof value !== 'string') throw new StrategiesError(`the ${field} of "${name}" must be text`);
    const limit = field === 'entry' || field === 'exit' ? MAX_FORMULA : field === 'name' ? MAX_NAME : MAX_NUMBER;
    if (value.length > limit) throw new StrategiesError(`the ${field} of "${name}" is at most ${limit} characters`);
    form[field] = value;
  }
  form.name = name;
  return { name, form };
}

export function validateStrategies(raw: unknown): StrategiesDoc {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new StrategiesError('the saved strategies are not a document');
  const d = raw as Record<string, unknown>;
  if (d.version !== 1) throw new StrategiesError('the saved strategies have an unknown version');
  const list = Array.isArray(d.strategies) ? d.strategies : [];
  if (list.length > MAX_STRATEGIES) throw new StrategiesError(`at most ${MAX_STRATEGIES} strategies can be saved`);
  const strategies = list.map(validOne);
  const seen = new Set<string>();
  for (const s of strategies) {
    const key = s.name.toLowerCase();
    if (seen.has(key)) throw new StrategiesError(`the name "${s.name}" is used twice`);
    seen.add(key);
  }
  return { version: 1, strategies };
}

export function saveStrategy(doc: StrategiesDoc, strategy: SavedStrategy): StrategiesDoc {
  const clean = validOne(strategy);
  const at = doc.strategies.findIndex((s) => s.name.toLowerCase() === clean.name.toLowerCase());
  if (at < 0 && doc.strategies.length >= MAX_STRATEGIES) throw new StrategiesError(`at most ${MAX_STRATEGIES} strategies can be saved`);
  const strategies = [...doc.strategies];
  if (at < 0) strategies.push(clean);
  else strategies[at] = clean;
  return { version: 1, strategies };
}

export const deleteStrategy = (doc: StrategiesDoc, name: string): StrategiesDoc => ({ version: 1, strategies: doc.strategies.filter((s) => s.name.toLowerCase() !== name.toLowerCase()) });

export interface StrategiesIO {
  load(): Promise<unknown | null>;
  save(body: string): Promise<void>;
}

export function serverStrategiesIO(fetcher: typeof fetch = fetch): StrategiesIO {
  return {
    async load() {
      const r = await fetcher('/api/strategies');
      if (r.status === 204 || r.status === 404) return null;
      if (!r.ok) throw new Error(`strategies: HTTP ${r.status}`);
      return r.json();
    },
    async save(body) {
      const r = await fetcher('/api/strategies', { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
      if (!r.ok) throw new Error(`strategies: HTTP ${r.status}`);
    },
  };
}

export class SavedStrategies {
  doc: StrategiesDoc = { version: 1, strategies: [] };
  problem: string | null = null;

  constructor(private io: StrategiesIO) {}

  async load(): Promise<void> {
    let raw: unknown;
    try {
      raw = await this.io.load();
    } catch {
      this.problem = 'Saved strategies could not be loaded: the server did not answer.';
      return;
    }
    if (raw === null) return;
    try {
      this.doc = validateStrategies(raw);
      this.problem = null;
    } catch (e) {
      this.problem = `Saved strategies could not be read (${e instanceof Error ? e.message : 'unknown'}); starting with none.`;
    }
  }

  names(): string[] {
    return this.doc.strategies.map((s) => s.name);
  }

  get(name: string): SavedStrategy | undefined {
    return this.doc.strategies.find((s) => s.name.toLowerCase() === name.toLowerCase());
  }

  async save(strategy: SavedStrategy): Promise<void> {
    this.doc = saveStrategy(this.doc, strategy);
    await this.io.save(JSON.stringify(this.doc));
  }

  async remove(name: string): Promise<void> {
    this.doc = deleteStrategy(this.doc, name);
    await this.io.save(JSON.stringify(this.doc));
  }
}
