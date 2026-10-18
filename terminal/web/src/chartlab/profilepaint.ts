import type { Profile } from './profile';

export interface ProfileColours {
  up: string;
  down: string;
  poc: string;
}

/** The share of the pane's width the longest row may take. */
export const MAX_SHARE = 0.3;

/** Paint a profile along the right edge of the price pane: one bar per price row, falling volume on the edge and rising volume beside it, the rows of the value
 *  area at full strength and the others faded, and a dashed line across the pane at the point of control. `y` maps a price to the pane's vertical pixel (null when
 *  the price is off the scale). The painter makes no decision about which bars are in view; the caller did. */
export function paintProfile(ctx: CanvasRenderingContext2D, size: { w: number; h: number }, profile: Profile, y: (price: number) => number | null, colours: ProfileColours): void {
  const longest = Math.max(...profile.rows.map((r) => r.total));
  if (!(longest > 0)) return;
  const room = size.w * MAX_SHARE;
  ctx.save();
  profile.rows.forEach((row, i) => {
    const top = y(row.hi);
    const bottom = y(row.lo);
    if (top === null || bottom === null) return;
    const height = Math.max(1, Math.abs(bottom - top) - 1);
    const y0 = Math.min(top, bottom);
    ctx.globalAlpha = i >= profile.areaLow && i <= profile.areaHigh ? 0.6 : 0.28;
    const down = (row.down / longest) * room;
    const up = (row.up / longest) * room;
    ctx.fillStyle = colours.down;
    ctx.fillRect(size.w - down, y0, down, height);
    ctx.fillStyle = colours.up;
    ctx.fillRect(size.w - down - up, y0, up, height);
  });
  const poc = profile.rows[profile.poc];
  const line = y((poc.lo + poc.hi) / 2);
  if (line !== null) {
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = colours.poc;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(0, Math.round(line) + 0.5);
    ctx.lineTo(size.w, Math.round(line) + 0.5);
    ctx.stroke();
  }
  ctx.restore();
}
