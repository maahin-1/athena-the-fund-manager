import type { Bar } from '../bars';
import type { Series } from '../chartlab/maths';
import { setCustom, type IndicatorDef } from '../chartlab/registry';

/** Python indicator files the user placed in a folder, as chart indicators under the id `py:<file>`. The server lists a file without running it, runs it in a
 *  process of its own only after the user has switched on that exact version, and the page asks for the numbers once for a set of bars: a chart draws the
 *  indicator's lines when they arrive. A file is the user's own code; it is not sandboxed, and the page says so where the switch is. */

export interface PyFile {
  file: string;
  name: string;
  pane: 'overlay' | 'own';
  sha256: string | null;
  size: number;
  /** Why the file cannot be used (a syntax error, too large), or null. */
  error: string | null;
  enabled: boolean;
  /** Was switched on, but the file on disk is not the version that was. */
  changed: boolean;
}

export interface PyListing {
  dir: string;
  indicators: PyFile[];
}

export type RunReply = { lines: Record<string, (number | null)[]> } | { error: string };

export interface PyIO {
  list(): Promise<PyListing>;
  /** Switch a file on for the version with this hash: null when done, or the server's reason when it refused. */
  enable(file: string, sha256: string): Promise<string | null>;
  disable(file: string): Promise<void>;
  run(file: string, bars: Bar[]): Promise<RunReply>;
}

export function serverPyIO(fetcher: typeof fetch = fetch): PyIO {
  const json = { 'content-type': 'application/json' };
  const reason = async (r: Response): Promise<string> => ((await r.json().catch(() => ({}))) as { error?: string }).error ?? `HTTP ${r.status}`;
  return {
    async list() {
      const r = await fetcher('/api/pyind');
      if (!r.ok) throw new Error(`pyind: HTTP ${r.status}`);
      return (await r.json()) as PyListing;
    },
    async enable(file, sha256) {
      const r = await fetcher('/api/pyind/enable', { method: 'PUT', headers: json, body: JSON.stringify({ file, sha256 }) });
      return r.ok ? null : reason(r);
    },
    async disable(file) {
      const r = await fetcher('/api/pyind/disable', { method: 'PUT', headers: json, body: JSON.stringify({ file }) });
      if (!r.ok) throw new Error(await reason(r));
    },
    async run(file, bars) {
      try {
        const r = await fetcher('/api/pyind/run', { method: 'POST', headers: json, body: JSON.stringify({ file, bars: bars.map((b) => ({ t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v })) }) });
        if (r.ok) return (await r.json()) as RunReply;
        return { error: await reason(r) };
      } catch {
        return { error: 'the server did not answer' };
      }
    },
  };
}

/** What a set of bars is, for remembering an indicator's answer: the file's version and the bars' count and first and last times. The last bar's prices are
 *  left out on purpose, so a live tick does not ask again; a new bar does. */
export const cacheKey = (file: string, sha: string, bars: Bar[]): string => `${file}|${sha}|${bars.length}|${bars[0]?.t ?? 0}|${bars[bars.length - 1]?.t ?? 0}`;

export const LOCAL_KEY = 'athena.pyind';
const COLOURS = ['--ind1', '--ind2', '--ind3'];

interface KeyValue {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export class PyIndicators {
  dir = '';
  /** Set when the folder could not be listed. */
  problem: string | null = null;
  private list: PyFile[] = [];
  private lines = new Map<string, Record<string, Series>>();
  private pending = new Set<string>();
  private outputs = new Map<string, IndicatorDef['outputs']>();
  private listeners = new Set<() => void>();
  private errorListeners = new Set<(file: string, message: string) => void>();

  constructor(private io: PyIO, private local: KeyValue | null) {
    try {
      const text = this.local?.getItem(LOCAL_KEY);
      if (text) this.list = (JSON.parse(text) as PyFile[]).filter((f) => f && typeof f.file === 'string' && /^[A-Za-z0-9_ -]{1,60}\.py$/.test(f.file));
    } catch {
      /* a damaged or blocked browser copy is as good as none */
    }
    this.register();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onError(fn: (file: string, message: string) => void): () => void {
    this.errorListeners.add(fn);
    return () => this.errorListeners.delete(fn);
  }

  files(): PyFile[] {
    return this.list;
  }

  file(name: string): PyFile | undefined {
    return this.list.find((f) => f.file === name);
  }

  private emit(): void {
    for (const fn of [...this.listeners]) fn();
  }

  /** Make an indicator of every usable file (switched on or not: a chart may have been saved with one). */
  private register(): void {
    setCustom(
      this.list.filter((f) => !f.error).map((f) => this.def(f.file)),
      'python',
    );
  }

  private def(fileName: string): IndicatorDef {
    const self = this;
    const first = this.file(fileName)!;
    return {
      id: `py:${fileName}`,
      name: first.name,
      pane: first.pane === 'overlay' ? 'overlay' : 'own',
      inputs: [],
      /** A file's lines are only known once it has run. */
      get outputs() {
        return self.outputs.get(fileName) ?? [];
      },
      compute(bars: Bar[]) {
        const now = self.file(fileName);
        if (!now || !now.enabled || !now.sha256 || !bars.length) return {};
        const key = cacheKey(fileName, now.sha256, bars);
        const hit = self.lines.get(key);
        if (hit) return hit;
        void self.request(fileName, key, bars);
        return {};
      },
    };
  }

  private async request(file: string, key: string, bars: Bar[]): Promise<void> {
    if (this.pending.has(key)) return;
    this.pending.add(key);
    const reply = await this.io.run(file, bars);
    this.pending.delete(key);
    if ('error' in reply) {
      for (const fn of [...this.errorListeners]) fn(file, reply.error);
      return;
    }
    this.lines.set(key, reply.lines);
    this.outputs.set(
      file,
      Object.keys(reply.lines).map((k, i) => ({ key: k, label: k, color: COLOURS[i % COLOURS.length], kind: 'line' as const, width: 2 as const })),
    );
    this.emit();
  }

  async load(): Promise<void> {
    try {
      const listing = await this.io.list();
      this.dir = listing.dir;
      this.list = listing.indicators;
      this.problem = null;
      try {
        this.local?.setItem(LOCAL_KEY, JSON.stringify(this.list));
      } catch {
        /* the browser copy is a convenience */
      }
    } catch {
      this.problem = 'The Python indicator folder could not be listed: the server did not answer.';
      return this.emit();
    }
    this.register();
    this.emit();
  }

  /** Switch a file on for the version the listing shows (the one the person was shown). Returns the reason when it is refused. */
  async enable(name: string): Promise<string | null> {
    const f = this.file(name);
    if (!f || !f.sha256) return 'that file is not listed';
    if (f.error) return `${name} has an error and cannot be switched on: ${f.error}`;
    const refused = await this.io.enable(name, f.sha256);
    if (refused) return refused;
    await this.load();
    return null;
  }

  async disable(name: string): Promise<void> {
    await this.io.disable(name);
    await this.load();
  }
}
