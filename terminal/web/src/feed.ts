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
  latencyMs: number | null;
}

export interface SocketLike {
  send(data: string): void;
  close(): void;
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
}

type QuoteListener = (quotes: Quote[]) => void;
type StatusListener = (status: FeedStatus) => void;

/** The page's one connection to the server. Panels say which symbols they need; the feed sends the union and nothing more often than it changes. */
export class Feed {
  readonly quotes = new Map<string, Quote>();
  status: FeedStatus = { socket: 'closed', feed: 'connecting', latencyMs: null };
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
      this.update({ socket: 'open' });
      this.flush();
    };
    socket.onclose = () => {
      this.socket = null;
      this.update({ socket: 'closed', latencyMs: null });
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
    const symbols = this.union();
    const key = symbols.join(',');
    if (key === this.lastSent || this.socket === null || this.status.socket !== 'open') return;
    this.lastSent = key;
    this.socket.send(JSON.stringify({ type: 'watch', symbols }));
  }

  private update(patch: Partial<FeedStatus>): void {
    this.status = { ...this.status, ...patch };
    this.statusListeners.forEach((fn) => fn(this.status));
  }

  private receive(text: string): void {
    let m: { type?: string; q?: Quote[]; sm?: number; feed?: FeedStatus['feed']; reason?: string };
    try {
      m = JSON.parse(text);
    } catch {
      return;
    }
    if (m.type === 'q' && Array.isArray(m.q)) {
      for (const q of m.q) this.quotes.set(q.s, q);
      if (typeof m.sm === 'number') {
        const d = Math.max(0, Date.now() - m.sm);
        const previous = this.status.latencyMs;
        this.update({ latencyMs: previous === null ? d : Math.round(previous * 0.8 + d * 0.2) });
      }
      this.quoteListeners.forEach((fn) => fn(m.q as Quote[]));
    } else if (m.type === 'status' && m.feed) {
      this.update({ feed: m.feed, reason: m.reason });
    }
  }
}
