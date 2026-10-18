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

/** One Kagi line: `o` is where it starts (where the last one ended), `c` where it ends now. `thick` is its weight at its end (yang thick, yin thin); `flip`
 *  is the price inside the line where it changed weight, when it did. */
export interface KagiLine extends Bar {
  thick: boolean;
  flip?: number;
}

/** Kagi lines from closes: a line runs on while the close goes on in its direction, and turns only when the close comes back by `reversal`, the new
 *  line starting where the old one ended. A line turns thick (yang) once it rises through the previous swing high and thin (yin) once it falls
 *  through the previous swing low, changing weight at that price. The first line is thick when it rises and thin when it falls. Each line carries the
 *  stamp of the source bar that last moved it and the volume of the source bars that moved it. */
export function kagi(bars: Bar[], reversal: number): KagiLine[] {
  const out: KagiLine[] = [];
  if (!(reversal > 0) || bars.length < 2) return out;
  const first = bars[0].c;
  let shoulder: number | null = null; // end of the last line up before the line now running down: the high to rise through
  let waist: number | null = null; // end of the last line down before the line now running up: the low to fall through
  const reweigh = (line: KagiLine, up: boolean): void => {
    if (up && !line.thick && shoulder !== null && line.c > shoulder) {
      line.thick = true;
      line.flip = shoulder;
    } else if (!up && line.thick && waist !== null && line.c < waist) {
      line.thick = false;
      line.flip = waist;
    }
  };
  for (const b of bars.slice(1)) {
    const cur = out[out.length - 1];
    if (!cur) {
      if (Math.abs(b.c - first) < reversal) continue;
      out.push({ t: b.t, o: first, h: Math.max(first, b.c), l: Math.min(first, b.c), c: b.c, v: b.v, thick: b.c > first });
      continue;
    }
    const up = cur.c >= cur.o;
    if ((up && b.c > cur.c) || (!up && b.c < cur.c)) {
      cur.c = b.c;
      cur.t = b.t;
      cur.h = Math.max(cur.o, cur.c);
      cur.l = Math.min(cur.o, cur.c);
      cur.v += b.v;
      reweigh(cur, up);
    } else if (Math.abs(b.c - cur.c) >= reversal) {
      if (up) shoulder = cur.c;
      else waist = cur.c;
      const next: KagiLine = { t: b.t, o: cur.c, h: Math.max(cur.c, b.c), l: Math.min(cur.c, b.c), c: b.c, v: b.v, thick: cur.thick };
      out.push(next);
      reweigh(next, !up);
    }
  }
  return out;
}

/** One point-and-figure column: `l` and `h` are the bottom edge of its lowest box and the top edge of its highest. `up` columns are X (open at the
 *  bottom, close at the top), the others O (open at the top, close at the bottom). `box` is the box size. */
export interface PnfColumn extends Bar {
  up: boolean;
  box: number;
}

/** Point-and-figure columns from closes: prices are put into boxes of `box`; a column of X rises while closes reach new boxes, and turns into a column
 *  of O when the close comes back `reversal` boxes (the O column starts one box under the top X); the same the other way round. The first column
 *  starts in the first close's box once a close has moved a whole box away from it. */
export function pointAndFigure(bars: Bar[], box: number, reversal = 3): PnfColumn[] {
  const out: PnfColumn[] = [];
  if (!(box > 0) || bars.length < 2) return out;
  const cell = (price: number): number => Math.floor(price / box + 1e-9);
  const start = cell(bars[0].c);
  let up = true;
  let lo = start;
  let hi = start;
  let begun = false;
  let t = bars[0].t;
  let v = 0;
  const seal = (): void => {
    const l = lo * box;
    const h = (hi + 1) * box;
    out.push({ t, o: up ? l : h, h, l, c: up ? h : l, v, up, box });
  };
  for (const b of bars.slice(1)) {
    const c = cell(b.c);
    if (!begun) {
      if (c === start) continue;
      begun = true;
      up = c > start;
      if (up) hi = c;
      else lo = c;
      t = b.t;
      v = b.v;
    } else if (up && c > hi) {
      hi = c;
      t = b.t;
      v += b.v;
    } else if (!up && c < lo) {
      lo = c;
      t = b.t;
      v += b.v;
    } else if (up && hi - c >= reversal) {
      seal();
      up = false;
      hi -= 1; // the O column starts one box under the top X
      lo = c;
      t = b.t;
      v = b.v;
    } else if (!up && c - lo >= reversal) {
      seal();
      up = true;
      lo += 1; // the X column starts one box over the bottom O
      hi = c;
      t = b.t;
      v = b.v;
    }
  }
  if (begun) seal();
  return out;
}

/** Give each derived bar a stamp one step after the one before, so a time axis shows them in order: derived bars have no calendar position. */
export function sequence<T extends Bar>(bars: T[], start: number, step: number): T[] {
  return bars.map((b, i) => ({ ...b, t: start + i * step }));
}
