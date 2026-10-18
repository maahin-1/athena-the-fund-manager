import { newDrawing, TOOLS, type Anchor, type Drawing, type Style, type ToolId } from './model';

/** Placing a new drawing, click by click. `begin` chooses the tool, `click` places an anchor and returns the drawing when the last one is down, `move` follows the pointer so a
 *  preview can be drawn, `cancel` drops what is half done. A position tool takes two clicks (entry, then target) and puts the stop at the mirror price. */
export class Creator {
  private tool: ToolId | null = null;
  private placed: Anchor[] = [];
  private cursor: Anchor | null = null;

  begin(tool: ToolId): void {
    this.tool = tool;
    this.placed = [];
    this.cursor = null;
  }

  get active(): ToolId | null {
    return this.tool;
  }

  /** Anchors placed so far (not counting the pointer). */
  get count(): number {
    return this.placed.length;
  }

  cancel(): void {
    this.tool = null;
    this.placed = [];
    this.cursor = null;
  }

  move(a: Anchor): void {
    this.cursor = a;
  }

  /** The drawing as it would be if the next click were at the pointer; null until there is a tool and a pointer position. */
  preview(style: Partial<Style> = {}): Drawing | null {
    if (!this.tool || !this.cursor) return null;
    return this.build([...this.placed, this.cursor], style);
  }

  click(a: Anchor, style: Partial<Style> = {}): Drawing | null {
    if (!this.tool) return null;
    this.placed.push(a);
    if (this.placed.length < TOOLS[this.tool].clicks) return null;
    const done = this.build(this.placed, style);
    this.cancel();
    return done;
  }

  /** A drawing from the anchors given: more than the tool has is cut, fewer are filled by repeating the last. */
  private build(anchors: Anchor[], style: Partial<Style>): Drawing | null {
    if (!this.tool) return null;
    const info = TOOLS[this.tool];
    const some = anchors.slice(0, info.clicks);
    while (some.length < info.clicks) some.push(some[some.length - 1]);
    if (this.tool === 'long' || this.tool === 'short') {
      const [entry, target] = some;
      return newDrawing(this.tool, [{ ...entry }, { t: target.t, p: 2 * entry.p - target.p }, { ...target }], style);
    }
    return newDrawing(this.tool, some, style);
  }
}
