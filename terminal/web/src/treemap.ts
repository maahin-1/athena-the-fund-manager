/** A squarified treemap (Bruls, Huizing and van Wijk): areas in proportion to values, tiles kept near square. Pure geometry: it takes numbers and returns
 *  rectangles, so it is tested without a page. With groups, each group is a box (its area in proportion to its total) holding its own tiles under a label. */

export interface Item {
  id: string;
  value: number;
  group?: string;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Tile extends Rect {
  id: string;
  /** The group the tile belongs to ('' when the items were not grouped). */
  group: string;
}

export interface GroupBox extends Rect {
  name: string;
  total: number;
  /** Whether the box is big enough for its label, which sits in its top `labelHeight` pixels. */
  labelled: boolean;
}

export interface Layout {
  tiles: Tile[];
  /** The group boxes, largest first; empty when the items had no groups. */
  groups: GroupBox[];
}

export interface Options {
  /** Height reserved at the top of a group box for its label (default 16). */
  labelHeight?: number;
  /** Space kept between a group box's edge and its tiles (default 2). */
  gap?: number;
}

const drawable = (i: Item): boolean => Number.isFinite(i.value) && i.value > 0;

/** Largest first; items of equal value keep the order they came in. */
const bySize = <T extends { value: number }>(list: T[]): T[] => list.map((v, i) => [v, i] as const).sort((a, b) => b[0].value - a[0].value || a[1] - b[1]).map(([v]) => v);

/** The worst aspect ratio of a row of areas laid along a side of length `side`. */
function worst(row: number[], side: number): number {
  const sum = row.reduce((s, a) => s + a, 0);
  const max = Math.max(...row);
  const min = Math.min(...row);
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

/** Tile `values` (already largest first) into `box`, the areas scaled to fill it. */
function squarify<T extends { value: number }>(list: T[], box: Rect): { item: T; rect: Rect }[] {
  const out: { item: T; rect: Rect }[] = [];
  const total = list.reduce((s, i) => s + i.value, 0);
  if (!(total > 0) || !(box.w > 0) || !(box.h > 0)) return out;
  const scale = (box.w * box.h) / total;
  let { x, y, w, h } = box;
  let row: T[] = [];
  const place = (): void => {
    const sum = row.reduce((s, i) => s + i.value * scale, 0);
    if (w >= h) {
      const rowW = sum / h;
      let cy = y;
      for (const item of row) {
        const ih = (item.value * scale) / rowW;
        out.push({ item, rect: { x, y: cy, w: rowW, h: ih } });
        cy += ih;
      }
      x += rowW;
      w -= rowW;
    } else {
      const rowH = sum / w;
      let cx = x;
      for (const item of row) {
        const iw = (item.value * scale) / rowH;
        out.push({ item, rect: { x: cx, y, w: iw, h: rowH } });
        cx += iw;
      }
      y += rowH;
      h -= rowH;
    }
    row = [];
  };
  for (const item of list) {
    const side = Math.min(w, h);
    const areas = row.map((r) => r.value * scale);
    if (row.length && worst([...areas, item.value * scale], side) > worst(areas, side)) place();
    row.push(item);
  }
  if (row.length) place();
  return out;
}

/** Lay out `items` in a `width` by `height` box. Items with no drawable value (zero, negative, not a number) are left out. */
export function layout(items: Item[], width: number, height: number, options: Options = {}): Layout {
  const labelHeight = options.labelHeight ?? 16;
  const gap = options.gap ?? 2;
  const empty: Layout = { tiles: [], groups: [] };
  if (!(width > 0) || !(height > 0)) return empty;
  const real = items.filter(drawable);
  if (!real.length) return empty;
  const named = real.some((i) => i.group);
  if (!named) {
    return { tiles: squarify(bySize(real), { x: 0, y: 0, w: width, h: height }).map(({ item, rect }) => ({ id: item.id, group: '', ...rect })), groups: [] };
  }
  const byGroup = new Map<string, Item[]>();
  for (const item of real) {
    const key = item.group ?? '';
    byGroup.set(key, [...(byGroup.get(key) ?? []), item]);
  }
  const groupItems = [...byGroup.entries()].map(([name, list]) => ({ name, list, value: list.reduce((s, i) => s + i.value, 0) }));
  const tiles: Tile[] = [];
  const groups: GroupBox[] = [];
  for (const { item: g, rect } of squarify(bySize(groupItems), { x: 0, y: 0, w: width, h: height })) {
    const labelled = rect.h >= labelHeight + 2 * gap + 4 && rect.w >= 24;
    const top = labelled ? labelHeight : 0;
    const inner: Rect = { x: rect.x + gap, y: rect.y + top + gap, w: rect.w - 2 * gap, h: rect.h - top - 2 * gap };
    const usable = inner.w > 1 && inner.h > 1 ? inner : rect; // a box too small for padding is filled edge to edge
    groups.push({ name: g.name, total: g.value, labelled, ...rect });
    for (const { item, rect: r } of squarify(bySize(g.list), usable)) tiles.push({ id: item.id, group: g.name, ...r });
  }
  return { tiles, groups };
}
