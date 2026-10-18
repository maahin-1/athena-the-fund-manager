import { el } from '../panels/base';
import { COLUMNS, type Row } from '../screenlogic';
import { layout, type Item } from '../treemap';
import { colourFor, SCALES, scaleOf, type Scale } from './colour';
import { formatCell } from './format';

export interface HeatOptions {
  group: 'sector' | 'industry' | 'none';
  size: 'market_cap_cr' | 'traded_value_cr';
  colour: string;
  /** The most tiles drawn: the largest by size, the rest named in a note. */
  limit: number;
}

export const DEFAULT_HEAT: HeatOptions = { group: 'sector', size: 'market_cap_cr', colour: 'ret_1d', limit: 600 };

const SIZE_LABEL = { market_cap_cr: 'market cap', traded_value_cr: 'traded value' } as const;
const UNGROUPED = 'Not classified';

/** The market as a treemap: one tile per stock, its area the company's size, its colour a figure (a day's move, a month's, the distance from the 52-week
 *  high), boxed by sector or industry. The layout is `treemap.ts`; this paints it as positioned tiles, so each is an element that can be hovered and
 *  clicked. It draws the rows it is given (the Screen tab hands it the current result, so filters and queries apply). */
export class HeatmapView {
  readonly element = el('div', 'heatmap');
  private options: HeatOptions = { ...DEFAULT_HEAT };
  private rows: Row[] = [];
  private readonly note = el('div', 'note hm-note');
  private readonly legend = el('div', 'note hm-legend');
  private readonly canvas = el('div', 'hm-canvas');
  private readonly groupBox = el('select', 'picker');
  private readonly sizeBox = el('select', 'picker');
  private readonly colourBox = el('select', 'picker');

  /** `size` gives the pixel size to draw in (measured from the page by default, fixed in tests). */
  constructor(
    private onSelect: (symbol: string, row: Row) => void,
    private size: () => { w: number; h: number } = () => {
      const w = this.element.clientWidth || 1000;
      return { w, h: Math.max(420, Math.min(760, Math.round(w * 0.55))) };
    },
  ) {
    this.groupBox.setAttribute('aria-label', 'group by');
    this.groupBox.append(new Option('Group by sector', 'sector'), new Option('Group by industry', 'industry'), new Option('No groups', 'none'));
    this.sizeBox.setAttribute('aria-label', 'size by');
    this.sizeBox.append(new Option('Size by market cap', 'market_cap_cr'), new Option('Size by traded value', 'traded_value_cr'));
    this.colourBox.setAttribute('aria-label', 'colour by');
    this.colourBox.append(...SCALES.map((s) => new Option(`Colour by ${s.label}`, s.id)));
    for (const box of [this.groupBox, this.sizeBox, this.colourBox]) {
      box.addEventListener('change', () => {
        this.options = { ...this.options, group: this.groupBox.value as HeatOptions['group'], size: this.sizeBox.value as HeatOptions['size'], colour: this.colourBox.value };
        this.paint();
      });
    }
    const controls = el('div', 'hm-controls');
    controls.append(this.groupBox, this.sizeBox, this.colourBox);
    this.element.append(controls, this.legend, this.note, this.canvas);
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => this.element.isConnected && !this.element.hidden && this.paint()).observe(this.element);
  }

  /** Draw `rows`, changing any of the options named. */
  render(rows: Row[], options: Partial<HeatOptions> = {}): void {
    this.rows = rows;
    this.options = { ...this.options, ...options };
    this.groupBox.value = this.options.group;
    this.sizeBox.value = this.options.size;
    this.colourBox.value = scaleOf(this.options.colour).id;
    this.paint();
  }

  private paint(): void {
    const { group, size, limit } = this.options;
    const scale = scaleOf(this.options.colour);
    const sized = this.rows.filter((r) => typeof r[size] === 'number' && (r[size] as number) > 0);
    const shown = [...sized].sort((a, b) => (b[size] as number) - (a[size] as number)).slice(0, limit);
    this.legend.replaceChildren(this.key(scale));
    this.canvas.replaceChildren();
    if (!shown.length) {
      this.note.textContent = `No stock in this result has a ${SIZE_LABEL[size]} to size a tile by.`;
      this.canvas.style.height = '0';
      return;
    }
    this.note.textContent = sized.length > shown.length ? `Showing the largest ${shown.length} of ${sized.length.toLocaleString('en-IN')} by ${SIZE_LABEL[size]}.` : `${shown.length.toLocaleString('en-IN')} stocks, sized by ${SIZE_LABEL[size]}.`;
    const box = this.size();
    this.canvas.style.height = `${box.h}px`;
    const byId = new Map(shown.map((r) => [String(r.symbol), r]));
    const items: Item[] = shown.map((r) => ({ id: String(r.symbol), value: r[size] as number, group: group === 'none' ? undefined : String(r[group] || UNGROUPED) }));
    const { tiles, groups } = layout(items, box.w, box.h, { labelHeight: 16, gap: 2 });
    for (const g of groups) {
      const frame = el('div', 'hm-group');
      place(frame, g);
      if (g.labelled) frame.append(el('div', 'hm-glabel', g.name));
      this.canvas.append(frame);
    }
    for (const t of tiles) {
      const row = byId.get(t.id)!;
      const figure = row[scale.column];
      const tile = el('div', 'hm-tile');
      place(tile, t);
      tile.dataset.symbol = t.id;
      tile.style.background = colourFor(figure, scale);
      tile.title = this.tip(row, scale);
      if (t.w >= Math.max(40, t.id.length * 7 + 8) && t.h >= 26) { // a label only where the whole symbol fits
        tile.append(el('span', 'hm-sym', t.id));
        if (t.h >= 40) tile.append(el('span', 'hm-val', formatCell(figure ?? null, COLUMNS[scale.column]?.[1] ?? 'pct', scale.column)[0]));
      }
      tile.addEventListener('click', () => this.onSelect(t.id, row));
      this.canvas.append(tile);
    }
  }

  private key(scale: Scale): HTMLElement {
    const box = el('span', 'hm-key');
    const bar = el('span', 'hm-bar');
    bar.style.background = `linear-gradient(to right, ${colourFor(scale.lo, scale)}, ${colourFor((scale.lo + scale.hi) / 2, scale)}, ${colourFor(scale.hi, scale)})`;
    box.append(el('span', '', `${scale.label}: `), bar, el('span', '', ` ${scale.legend}; grey: no figure`));
    return box;
  }

  private tip(row: Row, scale: Scale): string {
    const where = [row.sector, row.industry].filter((x): x is string => typeof x === 'string' && x !== '').join(' › ');
    const cap = formatCell((row.market_cap_cr as number | null) ?? null, 'number0', 'market_cap_cr')[0];
    const fig = formatCell((row[scale.column] as number | null) ?? null, COLUMNS[scale.column]?.[1] ?? 'pct', scale.column)[0];
    return [`${row.symbol} · ${row.name ?? ''}`, where, `Market cap Rs ${cap} cr`, `${scale.label}: ${fig}`].filter(Boolean).join('\n');
  }
}

function place(node: HTMLElement, r: { x: number; y: number; w: number; h: number }): void {
  node.style.left = `${r.x.toFixed(1)}px`;
  node.style.top = `${r.y.toFixed(1)}px`;
  node.style.width = `${r.w.toFixed(1)}px`;
  node.style.height = `${r.h.toFixed(1)}px`;
}
