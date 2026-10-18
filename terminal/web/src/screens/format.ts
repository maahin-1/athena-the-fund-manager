import { compact, pct, price } from '../format';
import type { Cell, Style } from '../screenlogic';

/** A screen figure as text with the tone it is drawn in (`up`, `dn`, `flat` or none), by the column's style. A missing figure is a dash, never a zero. The
 *  Screen table and the peers table use it, so a number reads the same in both. */
export function formatCell(value: Cell, style: Style, column: string): [string, string] {
  if (value === null || value === undefined) return ['—', 'flat'];
  if (typeof value === 'string') return [value.replace(/_/g, ' '), column === 'trend' ? (value === 'aligned_up' ? 'up' : value === 'aligned_down' ? 'dn' : 'flat') : ''];
  switch (style) {
    case 'pct':
      return [pct(value * 100), column.startsWith('ret_') || column === 'sma50_vs_200' || column === 'revenue_growth' ? (value > 0 ? 'up' : value < 0 ? 'dn' : 'flat') : ''];
    case 'price':
      return [price(value), ''];
    case 'number0':
      return [Math.abs(value) >= 1e5 ? compact(value) : Math.round(value).toLocaleString('en-IN'), ''];
    default:
      return [value.toFixed(2), ''];
  }
}
