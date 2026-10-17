import type { ChartType, LabState, ScaleMode } from './model';
import { find } from './registry';

const TYPES: ChartType[] = ['candles', 'hollow', 'bars', 'line', 'area', 'heikin'];
const SCALES: ScaleMode[] = ['auto', 'log', 'percent'];
const NAME = /^[A-Za-z0-9 _-]{1,40}$/;
const KEY = 'athena.chartlab.templates';
const LIMIT = 50;

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export class StateError extends Error {}

export function serialize(state: LabState): string {
  return JSON.stringify({ version: 1, ...state });
}

/** Read a saved state back. Anything of the wrong shape, an unknown indicator or a bad value is refused with a reason. */
export function deserialize(text: string): LabState {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new StateError('not valid JSON');
  }
  if (!isObject(raw) || raw.version !== 1) throw new StateError('not a chart state');
  if (!TYPES.includes(raw.type as ChartType)) throw new StateError('unknown chart type');
  if (!SCALES.includes(raw.scale as ScaleMode)) throw new StateError('unknown scale');
  if (typeof raw.adjusted !== 'boolean') throw new StateError('adjusted must be true or false');
  if (!(raw.compare === null || (typeof raw.compare === 'string' && /^[A-Za-z0-9&^ -]{1,24}$/.test(raw.compare)))) throw new StateError('bad compare symbol');
  if (!Array.isArray(raw.indicators) || raw.indicators.length > 40) throw new StateError('indicators must be a list of at most 40');
  const indicators = raw.indicators.map((entry) => {
    if (!isObject(entry) || typeof entry.id !== 'string' || !find(entry.id)) throw new StateError(`unknown indicator ${isObject(entry) ? String(entry.id) : '?'}`);
    if (!isObject(entry.params) || !Object.values(entry.params).every((v) => typeof v === 'number' || typeof v === 'string')) throw new StateError('bad indicator settings');
    const colors = isObject(entry.colors) ? entry.colors : {};
    if (!Object.values(colors).every((c) => typeof c === 'string' && /^#[0-9a-fA-F]{6}$/.test(c))) throw new StateError('colours must be #rrggbb');
    return { id: entry.id, params: entry.params as Record<string, number | string>, hidden: entry.hidden === true, colors: colors as Record<string, string> };
  });
  return { type: raw.type as ChartType, scale: raw.scale as ScaleMode, adjusted: raw.adjusted, indicators, compare: raw.compare as string | null };
}

/** Undo and redo over whole states, newest last, at most 50 deep. A new push after an undo drops what could have been redone. */
export class History<T> {
  private past: T[] = [];
  private future: T[] = [];

  constructor(private now: T) {}

  get current(): T {
    return this.now;
  }

  push(state: T): void {
    this.past.push(this.now);
    if (this.past.length > LIMIT) this.past.shift();
    this.now = state;
    this.future = [];
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  undo(): T | null {
    const back = this.past.pop();
    if (back === undefined) return null;
    this.future.push(this.now);
    this.now = back;
    return back;
  }

  redo(): T | null {
    const ahead = this.future.pop();
    if (ahead === undefined) return null;
    this.past.push(this.now);
    this.now = ahead;
    return ahead;
  }

  reset(state: T): void {
    this.past = [];
    this.future = [];
    this.now = state;
  }
}

/** The slice of `Storage` the templates use, so tests need no browser. */
export interface KeyValue {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** Named indicator sets kept in the browser's storage. When the storage is missing or throws they live in memory for the session. */
export class Templates {
  private memory: Record<string, string> = {};

  constructor(private storage: KeyValue | null) {
    try {
      const text = storage?.getItem(KEY);
      if (text) this.memory = this.valid(JSON.parse(text));
    } catch {
      /* storage can be blocked or hold something else; start empty */
    }
  }

  private valid(raw: unknown): Record<string, string> {
    if (!isObject(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter((entry): entry is [string, string] => NAME.test(entry[0]) && typeof entry[1] === 'string'));
  }

  private write(): void {
    try {
      this.storage?.setItem(KEY, JSON.stringify(this.memory));
    } catch {
      /* the templates stay in memory */
    }
  }

  static nameOk(name: string): boolean {
    return NAME.test(name);
  }

  list(): string[] {
    return Object.keys(this.memory).sort((a, b) => a.localeCompare(b));
  }

  save(name: string, state: LabState): void {
    if (!NAME.test(name)) throw new StateError('a name is 1 to 40 letters, digits, spaces, - or _');
    this.memory[name] = serialize(state);
    this.write();
  }

  load(name: string): LabState {
    const text = this.memory[name];
    if (text === undefined) throw new StateError(`no template named "${name}"`);
    return deserialize(text);
  }

  remove(name: string): void {
    delete this.memory[name];
    this.write();
  }
}
