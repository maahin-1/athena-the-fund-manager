import type { Pt, Shape } from './geometry';
import type { Dash } from './model';

const DASHES: Record<Dash, number[]> = { solid: [], dashed: [6, 4], dotted: [2, 3] };

function stroke(ctx: CanvasRenderingContext2D, color: string, width: number, dash: Dash): boolean {
  if (width <= 0) return false;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(DASHES[dash]);
  ctx.stroke();
  return true;
}

/** Draw shapes on a 2D canvas context in pane (CSS pixel) coordinates. */
export function paintShapes(ctx: CanvasRenderingContext2D, shapes: Shape[]): void {
  for (const s of shapes) {
    ctx.save();
    ctx.beginPath();
    switch (s.k) {
      case 'line':
        ctx.moveTo(s.x1, s.y1);
        ctx.lineTo(s.x2, s.y2);
        stroke(ctx, s.color, s.width, s.dash);
        break;
      case 'rect':
        ctx.rect(s.x, s.y, s.w, s.h);
        if (s.fill) {
          ctx.fillStyle = s.fill;
          ctx.fill();
        }
        stroke(ctx, s.color, s.width, s.dash);
        break;
      case 'ellipse':
        ctx.ellipse(s.cx, s.cy, Math.max(s.rx, 0.5), Math.max(s.ry, 0.5), 0, 0, Math.PI * 2);
        if (s.fill) {
          ctx.fillStyle = s.fill;
          ctx.fill();
        }
        stroke(ctx, s.color, s.width, s.dash);
        break;
      case 'poly':
        s.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        if (s.closed) ctx.closePath();
        if (s.fill) {
          ctx.fillStyle = s.fill;
          ctx.fill();
        }
        stroke(ctx, s.color, s.width, s.dash);
        break;
      case 'text': {
        ctx.font = `${s.size}px 'Segoe UI', system-ui, sans-serif`;
        ctx.textBaseline = 'alphabetic';
        const w = ctx.measureText(s.text).width;
        const left = s.align === 'left' ? s.x : s.align === 'right' ? s.x - w : s.x - w / 2;
        if (s.bg) {
          ctx.fillStyle = s.bg;
          ctx.fillRect(left - 3, s.y - s.size - 1, w + 6, s.size + 5);
        }
        ctx.fillStyle = s.color;
        ctx.fillText(s.text, left, s.y);
        break;
      }
    }
    ctx.restore();
  }
}

/** Square handles on the anchors of the selected drawing. */
export function paintHandles(ctx: CanvasRenderingContext2D, points: Pt[], accent: string, fill: string): void {
  ctx.save();
  ctx.setLineDash([]);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = accent;
  ctx.fillStyle = fill;
  for (const [x, y] of points) {
    ctx.beginPath();
    ctx.rect(x - 4, y - 4, 8, 8);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}
