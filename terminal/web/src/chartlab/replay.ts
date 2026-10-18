import { isIntradayId, parseInterval, stepOf } from '../interval';

/** Bar replay's pure parts: when a bar has ended, how many bars a moment reveals, the speeds, and the player that steps through the bars. A chart shows a bar
 *  only once the bar has ended by the replay moment, so nothing a bar will still do can be seen early. */

export interface Stamped {
  t: number;
}

/** Bars a second at each of the nine speeds, from one bar every ten seconds to fifty a second. */
export const SPEEDS = [0.1, 0.33, 1, 2, 3, 5, 10, 20, 50] as const;
export const DEFAULT_SPEED = 1;

/** 15:30 IST as seconds after midnight UTC of the date: the end of a trading day. */
const DAY_END = 10 * 3600;

/** When the bar at `i` has ended, in unix seconds: an intraday bar a bar-length after its start; a daily bar at 15:30 IST of its date; a bar of several days, a
 *  week or a month when the next one begins (a conservative end: it shows up when the next bar starts, never early), or a bar-length after its start for the
 *  last one. */
export function barEnd(bars: Stamped[], i: number, interval: string): number {
  const p = parseInterval(interval);
  const t = bars[i].t;
  if (!p.ok) return t + 86_400;
  if (isIntradayId(interval)) return t + stepOf(p);
  if (p.unit === 'D' && p.n === 1) return t + DAY_END;
  const next = bars[i + 1];
  return next ? next.t : t + stepOf(p);
}

/** How many bars (from the first) have ended by `moment`. Ends never decrease along the bars, so this is a binary search. */
export function revealedCount(bars: Stamped[], moment: number, interval: string): number {
  let lo = 0;
  let hi = bars.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (barEnd(bars, mid, interval) <= moment) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The moment a typed date stands for: the end of that trading day, so replay from it shows the day as it closed. Null for text that is not a real date. */
export function momentOfDate(text: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return null;
  return date.getTime() / 1000 + DAY_END;
}

export type Tick = 'stepped' | 'ended' | 'idle';

/** Where replay is, as the number of bars revealed (`shown`, at least 1 once started), and whether it is playing. It owns no timer: the page calls `tick`
 *  every `intervalMs` while `playing`. */
export class Player {
  playing = false;
  speed: number = DEFAULT_SPEED;

  constructor(readonly total: number, public shown: number) {
    this.shown = this.clamp(shown);
  }

  private clamp(n: number): number {
    return Math.min(this.total, Math.max(this.total ? 1 : 0, Math.round(n)));
  }

  get atEnd(): boolean {
    return this.shown >= this.total;
  }

  get intervalMs(): number {
    return 1000 / this.speed;
  }

  next(): boolean {
    if (this.atEnd) {
      this.playing = false;
      return false;
    }
    this.shown += 1;
    return true;
  }

  back(): boolean {
    this.playing = false; // going back is a decision to look, not to run
    if (this.shown <= 1) return false;
    this.shown -= 1;
    return true;
  }

  jump(shown: number): void {
    this.shown = this.clamp(shown);
    if (this.atEnd) this.playing = false;
  }

  play(): void {
    if (!this.atEnd) this.playing = true;
  }

  pause(): void {
    this.playing = false;
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  setSpeed(speed: number): boolean {
    if (!(SPEEDS as readonly number[]).includes(speed)) return false;
    this.speed = speed;
    return true;
  }

  /** One timer beat: reveal a bar while playing; reaching the end stops it. */
  tick(): Tick {
    if (!this.playing) return 'idle';
    this.next();
    if (this.atEnd) {
      this.playing = false;
      return 'ended';
    }
    return 'stepped';
  }
}
