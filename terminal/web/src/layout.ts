import { GROUPS, type Group } from './links';

export const PANEL_TYPES = ['watchlist', 'chart', 'depth', 'quote', 'tape', 'analysis', 'verdict'] as const;
export type PanelType = (typeof PANEL_TYPES)[number];

export interface PanelState {
  type: PanelType;
  group: Group;
  symbol?: string;
  range?: string; // a label from RANGES
  indicators?: string[];
}

/** Where a panel goes when a layout is built from a recipe (the presets) rather than from a saved dock. */
export interface Placement {
  id: string;
  type: PanelType;
  ref?: string;
  direction?: 'left' | 'right' | 'above' | 'below' | 'within'; // within: a tab of the reference panel's group
  size?: number; // pixels along the split
}

export interface LayoutModel {
  version: 1;
  dock: unknown | null; // dockview's own saved layout; null when `arrangement` is used
  arrangement: Placement[] | null;
  panels: Record<string, PanelState>;
  watchlist: string[];
  theme: 'dark' | 'light';
}

export const DEFAULT_WATCHLIST = ['SBIN', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'LUPIN', 'RVNL'];

export class LayoutError extends Error {}

export const serialize = (m: LayoutModel): string => JSON.stringify(m);

export function deserialize(text: string): LayoutModel {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new LayoutError('the layout is not valid JSON');
  }
  const m = data as Partial<LayoutModel>;
  if (typeof m !== 'object' || m === null || m.version !== 1) throw new LayoutError('unsupported layout version');
  if (typeof m.panels !== 'object' || m.panels === null) throw new LayoutError('the layout has no panels');
  for (const [id, p] of Object.entries(m.panels)) {
    if (!PANEL_TYPES.includes(p.type)) throw new LayoutError(`unknown panel type "${String(p.type)}" for ${id}`);
    if (!GROUPS.includes(p.group)) throw new LayoutError(`unknown link group for ${id}`);
  }
  if (!Array.isArray(m.watchlist) || !m.watchlist.every((s) => typeof s === 'string')) throw new LayoutError('the watchlist is not a list of symbols');
  return {
    version: 1,
    dock: m.dock ?? null,
    arrangement: m.arrangement ?? null,
    panels: m.panels,
    watchlist: m.watchlist,
    theme: m.theme === 'light' ? 'light' : 'dark',
  };
}

const base = (panels: Record<string, PanelState>, arrangement: Placement[]): LayoutModel => ({
  version: 1,
  dock: null,
  arrangement,
  panels,
  watchlist: [...DEFAULT_WATCHLIST],
  theme: 'dark',
});

export type PresetName = 'A' | 'B' | 'C' | 'D' | 'M';

/** A: one stock. B: a chart grid with a tape. C: dense, everything about one stock on one screen. D: research, the chart beside the stored analysis and verdict. */
export const PRESETS: Record<PresetName, () => LayoutModel> = {
  A: () =>
    base(
      {
        'chart-1': { type: 'chart', group: 'blue', symbol: 'SBIN', range: '1D', indicators: ['EMA 20'] },
        'watchlist-1': { type: 'watchlist', group: 'blue' },
        'depth-1': { type: 'depth', group: 'blue', symbol: 'SBIN' },
        'quote-1': { type: 'quote', group: 'blue', symbol: 'SBIN' },
        'tape-1': { type: 'tape', group: 'none' },
      },
      [
        { id: 'chart-1', type: 'chart' },
        { id: 'watchlist-1', type: 'watchlist', ref: 'chart-1', direction: 'left', size: 280 },
        { id: 'depth-1', type: 'depth', ref: 'chart-1', direction: 'right', size: 280 },
        { id: 'quote-1', type: 'quote', ref: 'depth-1', direction: 'below', size: 310 },
        { id: 'tape-1', type: 'tape', ref: 'chart-1', direction: 'below', size: 40 },
      ],
    ),
  B: () =>
    base(
      {
        'chart-1': { type: 'chart', group: 'red', symbol: 'SBIN', range: '1D' },
        'chart-2': { type: 'chart', group: 'yellow', symbol: 'RELIANCE', range: '1D' },
        'chart-3': { type: 'chart', group: 'green', symbol: 'TCS', range: '5D' },
        'chart-4': { type: 'chart', group: 'blue', symbol: 'NIFTY 50', range: '6M' },
        'tape-1': { type: 'tape', group: 'none' },
      },
      [
        { id: 'chart-1', type: 'chart' },
        { id: 'chart-2', type: 'chart', ref: 'chart-1', direction: 'right' },
        { id: 'chart-3', type: 'chart', ref: 'chart-1', direction: 'below' },
        { id: 'chart-4', type: 'chart', ref: 'chart-2', direction: 'below' },
        { id: 'tape-1', type: 'tape', ref: 'chart-3', direction: 'below', size: 40 },
      ],
    ),
  C: () =>
    base(
      {
        'quote-1': { type: 'quote', group: 'blue', symbol: 'SBIN' },
        'chart-1': { type: 'chart', group: 'blue', symbol: 'SBIN', range: '1D', indicators: ['EMA 20', 'RSI 14'] },
        'depth-1': { type: 'depth', group: 'blue', symbol: 'SBIN' },
        'watchlist-1': { type: 'watchlist', group: 'blue' },
        'tape-1': { type: 'tape', group: 'none' },
      },
      [
        { id: 'quote-1', type: 'quote' },
        { id: 'chart-1', type: 'chart', ref: 'quote-1', direction: 'right' },
        { id: 'depth-1', type: 'depth', ref: 'quote-1', direction: 'below', size: 260 },
        { id: 'watchlist-1', type: 'watchlist', ref: 'chart-1', direction: 'right', size: 300 },
        { id: 'tape-1', type: 'tape', ref: 'chart-1', direction: 'below', size: 40 },
      ],
    ),
  M: () =>
    base(
      {
        'chart-1': { type: 'chart', group: 'blue', symbol: 'SBIN', range: '1D', indicators: ['EMA 20'] },
        'watchlist-1': { type: 'watchlist', group: 'blue' },
        'quote-1': { type: 'quote', group: 'blue', symbol: 'SBIN' },
        'depth-1': { type: 'depth', group: 'blue', symbol: 'SBIN' },
        'tape-1': { type: 'tape', group: 'none' },
      },
      [
        { id: 'chart-1', type: 'chart' },
        { id: 'watchlist-1', type: 'watchlist', ref: 'chart-1', direction: 'below', size: 300 },
        { id: 'quote-1', type: 'quote', ref: 'watchlist-1', direction: 'within' },
        { id: 'depth-1', type: 'depth', ref: 'watchlist-1', direction: 'within' },
        { id: 'tape-1', type: 'tape', ref: 'chart-1', direction: 'above', size: 36 },
      ],
    ),
  D: () =>
    base(
      {
        'watchlist-1': { type: 'watchlist', group: 'blue' },
        'chart-1': { type: 'chart', group: 'blue', symbol: 'SBIN', range: '6M', indicators: ['EMA 20', 'SMA 50'] },
        'analysis-1': { type: 'analysis', group: 'blue', symbol: 'SBIN' },
        'verdict-1': { type: 'verdict', group: 'blue', symbol: 'SBIN' },
        'tape-1': { type: 'tape', group: 'none' },
      },
      [
        { id: 'chart-1', type: 'chart' },
        { id: 'watchlist-1', type: 'watchlist', ref: 'chart-1', direction: 'left', size: 280 },
        { id: 'analysis-1', type: 'analysis', ref: 'chart-1', direction: 'right', size: 420 },
        { id: 'verdict-1', type: 'verdict', ref: 'analysis-1', direction: 'below', size: 380 },
        { id: 'tape-1', type: 'tape', ref: 'chart-1', direction: 'below', size: 40 },
      ],
    ),
};

export const PRESET_NAMES: PresetName[] = ['A', 'B', 'C', 'D', 'M'];

/** A window narrower than this gets the one-column arrangement M. */
export const NARROW_PX = 760;
