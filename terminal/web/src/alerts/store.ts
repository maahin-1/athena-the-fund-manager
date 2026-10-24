import type { Quote } from '../feed';
import { readAlerts, step, type Alert, type Fired } from './model';

export const KEY = 'athena.alerts';

interface KeyValue {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** The alerts the page watches: kept in the browser, evaluated on every quote, and told to whoever listens when one fires. */
export class AlertStore {
  alerts: Alert[];
  private listeners = new Set<() => void>();
  private onFire = new Set<(f: Fired) => void>();

  constructor(private storage: KeyValue | null, private clock: () => number = Date.now) {
    this.alerts = this.load();
  }

  private load(): Alert[] {
    try {
      return readAlerts(JSON.parse(this.storage?.getItem(KEY) ?? 'null'));
    } catch {
      return [];
    }
  }

  private save(): void {
    try {
      this.storage?.setItem(KEY, JSON.stringify(this.alerts));
    } catch {
      /* a full or blocked store loses the saved copy, not the alerts in memory */
    }
  }

  /** Symbols with a live alert: the feed must send these. */
  symbols(): string[] {
    return [...new Set(this.alerts.filter((a) => !a.done).map((a) => a.symbol))];
  }

  add(alert: Alert): void {
    this.alerts = [...this.alerts, alert];
    this.changed();
  }

  remove(id: string): void {
    this.alerts = this.alerts.filter((a) => a.id !== id);
    this.changed();
  }

  clearDone(): void {
    this.alerts = this.alerts.filter((a) => !a.done);
    this.changed();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onFired(fn: (f: Fired) => void): () => void {
    this.onFire.add(fn);
    return () => this.onFire.delete(fn);
  }

  /** Feed the quotes in; returns what fired. */
  quotes(quotes: readonly Quote[]): Fired[] {
    const fired: Fired[] = [];
    let moved = false;
    for (const q of quotes) {
      if (!this.alerts.some((a) => a.symbol === q.s && !a.done)) continue;
      const result = step(this.alerts, q, this.clock());
      if (result.alerts.some((a, i) => a !== this.alerts[i])) moved = true;
      this.alerts = result.alerts;
      fired.push(...result.fired);
    }
    if (moved) this.changed();
    for (const f of fired) for (const fn of this.onFire) fn(f);
    return fired;
  }

  private changed(): void {
    this.save();
    for (const fn of this.listeners) fn();
  }
}
