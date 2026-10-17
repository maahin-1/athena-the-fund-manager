import type { Bar } from '../bars';

/** Chart types built from price moves, not from time: each result bar stands for one brick or line, so its stamp is its position, not a date. */

/** Renko bricks of `size`: a new brick forms when the close moves a whole brick beyond the last one, and a reversal needs two (one more than the
 *  brick it turns back over). Each brick carries the volume of the bar that made it first. Bricks open where the last one closed (a reversal
 *  opens where the last one opened). */
export function renko(bars: Bar[], size: number): Bar[] {
  const out: Bar[] = [];
  if (!(size > 0) || !bars.length) return out;
  let level = Math.round(bars[0].c / size) * size; // close of the last brick
  let dir = 0;
  for (const b of bars) {
    let first = true;
    for (;;) {
      const upNeed = level + (dir >= 0 ? size : 2 * size);
      const downNeed = level - (dir <= 0 ? size : 2 * size);
      let open: number;
      let close: number;
      if (b.c >= upNeed) {
        open = dir >= 0 ? level : level + size;
        close = open + size;
        dir = 1;
      } else if (b.c <= downNeed) {
        open = dir <= 0 ? level : level - size;
        close = open - size;
        dir = -1;
      } else break;
      out.push({ t: b.t, o: open, h: Math.max(open, close), l: Math.min(open, close), c: close, v: first ? b.v : 0 });
      level = close;
      first = false;
    }
  }
  return out;
}

/** Three-line break (or `lines`-line break): a new line forms only when the close goes beyond the high or low of the last `lines` lines. Each new
 *  line opens at the previous line's close. */
export function lineBreak(bars: Bar[], lines = 3): Bar[] {
  const out: Bar[] = [];
  if (bars.length < 2) return out;
  let previous = bars[0].c;
  for (const b of bars.slice(1)) {
    if (!out.length) {
      if (b.c !== previous) out.push({ t: b.t, o: previous, h: Math.max(previous, b.c), l: Math.min(previous, b.c), c: b.c, v: b.v });
      continue;
    }
    const recent = out.slice(-lines);
    const high = Math.max(...recent.map((x) => x.h));
    const low = Math.min(...recent.map((x) => x.l));
    const last = out[out.length - 1];
    if (b.c > high || b.c < low) {
      previous = last.c;
      out.push({ t: b.t, o: previous, h: Math.max(previous, b.c), l: Math.min(previous, b.c), c: b.c, v: b.v });
    }
  }
  return out;
}

/** Range bars: every closed bar spans exactly `range` from low to high. A bar closes the moment price has moved `range` beyond its extreme (at the
 *  boundary, not at the price that crossed it) and the next opens where it closed. Source bars are walked as prices (open, then low and high in the
 *  order that suits their colour, then close), the usual approximation when the individual trades are not at hand. A source bar's volume goes to the
 *  bar that is open when it ends. */
export function rangeBars(bars: Bar[], range: number): Bar[] {
  const out: Bar[] = [];
  if (!(range > 0) || !bars.length) return out;
  let cur: Bar = { t: bars[0].t, o: bars[0].o, h: bars[0].o, l: bars[0].o, c: bars[0].o, v: 0 };
  for (const b of bars) {
    const path = b.c >= b.o ? [b.o, b.l, b.h, b.c] : [b.o, b.h, b.l, b.c];
    for (const price of path) {
      for (;;) {
        const hi = Math.max(cur.h, price);
        const lo = Math.min(cur.l, price);
        if (hi - lo < range) {
          cur.h = hi;
          cur.l = lo;
          cur.c = price;
          break;
        }
        const up: boolean = price > cur.c;
        const edge: number = up ? cur.l + range : cur.h - range;
        if (up) cur.h = edge;
        else cur.l = edge;
        cur.c = edge;
        out.push(cur);
        cur = { t: b.t, o: edge, h: edge, l: edge, c: edge, v: 0 };
      }
    }
    cur.v += b.v;
  }
  out.push(cur);
  return out;
}

/** Give each derived bar a stamp one step after the one before, so a time axis shows them in order: derived bars have no calendar position. */
export function sequence(bars: Bar[], start: number, step: number): Bar[] {
  return bars.map((b, i) => ({ ...b, t: start + i * step }));
}
