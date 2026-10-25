export interface Quote {
  s: string;
  l: number;
  pc: number;
  o: number | null;
  h: number | null;
  lo: number | null;
  v: number | null;
  avg: number | null;
  h52: number | null;
  l52: number | null;
  lb: number | null;
  ub: number | null;
  t: number | null;
  b: [number, number][];
  a: [number, number][];
  src: 'stream' | 'poll';
}

export interface FeedStatus {
  socket: 'connecting' | 'open' | 'closed';
  feed: 'connecting' | 'on' | 'off';
  reason?: string;
  /** Server to page: how long a frame took to arrive (smoothed). */
  latencyMs: number | null;
  /** Exchange to page: arrival time less the exchange's own time on the newest streamed quote of a frame (smoothed); null until a live tick is seen. */
  ageMs: number | null;
}

/** A future's or option's quote as the server pushes it (the same row the quotes route answers). */
export interface DerivRow {
  seg: string;
  token: string;
  last: number | null;
  bid: number | null;
  ask: number | null;
  volume: number;
  oi: number;
  oip: number;
  t: number | null;
}

/** A tick older than this is the last session's, not a measure of the feed's delay. */
export const AGE_LIVE_MS = 60_000;
/** Most contracts the server streams at once (`deriv_quotes::MAX_WANTED`). */
export const MAX_DERIVS = 300;

export interface SocketLike {
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

type QuoteListener = (quotes: Quote[]) => void;
type DerivListener = (rows: DerivRow[]) => void;
type StatusListener = (status: FeedStatus) => void;

/** The page's one connection to the server. Panels say which symbols they need; the feed sends the union and nothing more often than it changes. */
export class Feed {
  readonly quotes = new Map<string, Quote>();
  status: FeedStatus = { socket: 'closed', feed: 'connecting', latencyMs: null, ageMs: null };
  /** The newest pushed quote of each future and option a panel named, by `SEGMENT:TOKEN`. */
  readonly derivs = new Map<string, DerivRow>();
  private derivNeeds = new Map<string, Set<string>>();
  private lastDerivs = '';
  private derivListeners = new Set<DerivListener>();
  private socket: SocketLike | null = null;
  private needs = new Map<string, Set<string>>();
  private lastSent = '';
  private backoff = 500;
  private stopped = false;
  private quoteListeners = new Set<QuoteListener>();
  private statusListeners = new Set<StatusListener>();

  constructor(
    private url: string,
    private open: (url: string) => SocketLike = (u) => new WebSocket(u) as unknown as SocketLike,
    private setTimer: (fn: () => void, ms: number) => unknown = (fn, ms) => setTimeout(fn, ms),
  ) {}

  connect(): void {
    this.stopped = false;
    this.update({ socket: 'connecting' });
    const socket = this.open(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.backoff = 500;
      this.lastSent = '';
      this.lastDerivs = '';
      this.update({ socket: 'open' });
      this.flush();
    };
    socket.onclose = () => {
      this.socket = null;
      this.update({ socket: 'closed', latencyMs: null, ageMs: null });
      if (!this.stopped) {
        const wait = this.backoff;
        this.backoff = Math.min(this.backoff * 2, 10_000);
        this.setTimer(() => this.connect(), wait);
      }
    };
    socket.onmessage = (ev) => this.receive(String(ev.data));
  }

  close(): void {
    this.stopped = true;
    this.socket?.close();
  }

  /** Replace what one panel needs. An empty list is the same as `release`. */
  need(owner: string, symbols: string[]): void {
    if (symbols.length === 0) this.needs.delete(owner);
    else this.needs.set(owner, new Set(symbols.map((s) => s.toUpperCase())));
    this.flush();
  }

  release(owner: string): void {
    this.need(owner, []);
  }

  /** Replace the futures and options one panel needs (`SEGMENT:TOKEN`); the server then pushes each change. An empty list lets them go. */
  needDerivs(owner: string, keys: string[]): void {
    if (keys.length === 0) this.derivNeeds.delete(owner);
    else this.derivNeeds.set(owner, new Set(keys));
    this.flush();
  }

  onDerivs(fn: DerivListener): () => void {
    this.derivListeners.add(fn);
    return () => this.derivListeners.delete(fn);
  }

  /** Whether pushed prices can be relied on now: the socket is open and the server's broker stream is on. */
  get pushing(): boolean {
    return this.status.socket === 'open' && this.status.feed === 'on';
  }

  onQuotes(fn: QuoteListener): () => void {
    this.quoteListeners.add(fn);
    return () => this.quoteListeners.delete(fn);
  }

  onStatus(fn: StatusListener): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  private union(): string[] {
    const all = new Set<string>();
    this.needs.forEach((set) => set.forEach((s) => all.add(s)));
    return [...all].sort();
  }

  private flush(): void {
    if (this.socket === null || this.status.socket !== 'open') return;
    const symbols = this.union();
    const key = symbols.join(',');
    if (key !== this.lastSent) {
      this.lastSent = key;
      this.socket.send(JSON.stringify({ type: 'watch', symbols }));
    }
    const all = new Set<string>();
    this.derivNeeds.forEach((set) => set.forEach((k) => all.add(k)));
    const keys = [...all].sort().slice(0, MAX_DERIVS).join(',');
    if (keys !== this.lastDerivs) {
      this.lastDerivs = keys;
      this.socket.send(JSON.stringify({ type: 'derivs', keys }));
    }
  }

  /** Smooths the two delays: server to page from the frame's stamp, exchange to page from the newest exchange time in it (a time over a minute old is the last
   *  session's and says nothing about the feed). */
  private measure(sm: number | undefined, times: (number | null)[]): void {
    const now = Date.now();
    const smooth = (previous: number | null, d: number) => (previous === null ? d : Math.round(previous * 0.8 + d * 0.2));
    const patch: Partial<FeedStatus> = {};
    if (typeof sm === 'number') patch.latencyMs = smooth(this.status.latencyMs, Math.max(0, now - sm));
    const newest = Math.max(...times.filter((t): t is number => typeof t === 'number' && t > 0));
    if (Number.isFinite(newest) && now - newest >= 0 && now - newest <= AGE_LIVE_MS) patch.ageMs = smooth(this.status.ageMs, now - newest);
    if (Object.keys(patch).length) this.update(patch);
  }

  private update(patch: Partial<FeedStatus>): void {
    this.status = { ...this.status, ...patch };
    this.statusListeners.forEach((fn) => fn(this.status));
  }

  private receive(text: string): void {
    let m: { type?: string; q?: Quote[]; d?: DerivRow[]; sm?: number; feed?: FeedStatus['feed']; reason?: string };
    try {
      m = JSON.parse(text);
    } catch {
      return;
    }
    if (m.type === 'q' && Array.isArray(m.q)) {
      for (const q of m.q) this.quotes.set(q.s, q);
      this.measure(m.sm, m.q.filter((q) => q.src === 'stream').map((q) => q.t));
      this.quoteListeners.forEach((fn) => fn(m.q as Quote[]));
    } else if (m.type === 'd' && Array.isArray(m.d)) {
      for (const r of m.d) this.derivs.set(`${r.seg}:${r.token}`, r);
      this.measure(m.sm, m.d.map((r) => r.t));
      this.derivListeners.forEach((fn) => fn(m.d as DerivRow[]));
    } else if (m.type === 'status' && m.feed) {
      this.update({ feed: m.feed, reason: m.reason });
    }
  }
}
