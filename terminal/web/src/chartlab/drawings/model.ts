/** Drawings as plain data: what is drawn, where (bar times and prices, never pixels), how it looks, and the list operations. Every operation returns a new list and
 *  leaves the old one alone, so undo is a stack of lists. Nothing here touches the page. */

export type ToolId =
  | 'trend' | 'ray' | 'extended' | 'info' | 'hline' | 'hray' | 'vline' | 'cross'
  | 'channel' | 'flat'
  | 'fib' | 'fibext'
  | 'rect' | 'ellipse' | 'triangle'
  | 'text' | 'label'
  | 'measure' | 'pricerange' | 'daterange'
  | 'long' | 'short'
  // tier 2: more lines, channels, pitchforks, Fibonacci, Gann, shapes and markers
  | 'angle' | 'arrow' | 'disjoint'
  | 'pitchfork' | 'schiff' | 'mschiff'
  | 'fibchannel' | 'fibtime' | 'fibfan' | 'fibcircles' | 'fibarcs'
  | 'gannfan' | 'gannbox'
  | 'circle' | 'arrowup' | 'arrowdown' | 'forecast'
  // tier 3: patterns, Elliott waves, cycles
  | 'abcd' | 'xabcd' | 'cypher' | 'headshoulders' | 'threedrives' | 'trianglepat' | 'elliottimpulse' | 'elliottcorrective'
  | 'cyclic' | 'timecycles' | 'sine';

export type Group = 'lines' | 'channels' | 'pitchfork' | 'fib' | 'gann' | 'patterns' | 'cycles' | 'shapes' | 'notes' | 'measure' | 'position';

export interface ToolInfo {
  label: string;
  group: Group;
  anchors: number; // points a drawing holds
  clicks: number; // clicks that place them (a position tool derives its stop)
}

export const GROUPS: { id: Group; label: string }[] = [
  { id: 'lines', label: 'Lines' }, { id: 'channels', label: 'Channels' }, { id: 'pitchfork', label: 'Pitchforks' }, { id: 'fib', label: 'Fibonacci' }, { id: 'gann', label: 'Gann' },
  { id: 'patterns', label: 'Patterns and Elliott waves' }, { id: 'cycles', label: 'Cycles' }, { id: 'shapes', label: 'Shapes' },
  { id: 'notes', label: 'Annotation' }, { id: 'measure', label: 'Measure and forecast' }, { id: 'position', label: 'Position' },
];

const tool = (label: string, group: Group, anchors: number, clicks = anchors): ToolInfo => ({ label, group, anchors, clicks });

export const TOOLS: Record<ToolId, ToolInfo> = {
  trend: tool('Trend line', 'lines', 2), ray: tool('Ray', 'lines', 2), extended: tool('Extended line', 'lines', 2), info: tool('Info line', 'lines', 2),
  hline: tool('Horizontal line', 'lines', 1), hray: tool('Horizontal ray', 'lines', 1), vline: tool('Vertical line', 'lines', 1), cross: tool('Cross line', 'lines', 1),
  channel: tool('Parallel channel', 'channels', 3), flat: tool('Flat top/bottom', 'channels', 3),
  fib: tool('Fib retracement', 'fib', 2), fibext: tool('Trend-based fib extension', 'fib', 3),
  rect: tool('Rectangle', 'shapes', 2), ellipse: tool('Ellipse', 'shapes', 2), triangle: tool('Triangle', 'shapes', 3),
  text: tool('Text', 'notes', 1), label: tool('Price label', 'notes', 1),
  measure: tool('Measure', 'measure', 2), pricerange: tool('Price range', 'measure', 2), daterange: tool('Date range', 'measure', 2),
  long: tool('Long position', 'position', 3, 2), short: tool('Short position', 'position', 3, 2),
  angle: tool('Trend angle', 'lines', 2), arrow: tool('Arrow', 'lines', 2), disjoint: tool('Disjoint channel', 'channels', 4),
  pitchfork: tool('Pitchfork', 'pitchfork', 3), schiff: tool('Schiff pitchfork', 'pitchfork', 3), mschiff: tool('Modified Schiff pitchfork', 'pitchfork', 3),
  fibchannel: tool('Fib channel', 'fib', 3), fibtime: tool('Fib time zones', 'fib', 2), fibfan: tool('Fib speed fan', 'fib', 2), fibcircles: tool('Fib circles', 'fib', 2), fibarcs: tool('Fib arcs', 'fib', 2),
  gannfan: tool('Gann fan', 'gann', 2), gannbox: tool('Gann box', 'gann', 2),
  circle: tool('Circle', 'shapes', 2), arrowup: tool('Arrow marker up', 'notes', 1), arrowdown: tool('Arrow marker down', 'notes', 1), forecast: tool('Forecast', 'measure', 2),
  abcd: tool('ABCD pattern', 'patterns', 4), xabcd: tool('XABCD pattern', 'patterns', 5), cypher: tool('Cypher pattern', 'patterns', 5), headshoulders: tool('Head and shoulders', 'patterns', 7),
  threedrives: tool('Three drives', 'patterns', 7), trianglepat: tool('Triangle pattern', 'patterns', 4), elliottimpulse: tool('Elliott impulse wave (0 to 5)', 'patterns', 6),
  elliottcorrective: tool('Elliott correction (0, A, B, C)', 'patterns', 4),
  cyclic: tool('Cyclic lines', 'cycles', 2), timecycles: tool('Time cycles', 'cycles', 2), sine: tool('Sine line', 'cycles', 2),
};

export const TOOL_IDS = Object.keys(TOOLS) as ToolId[];
export const MAX_DRAWINGS = 500;
export const MAX_TEXT = 200;

export type Dash = 'solid' | 'dashed' | 'dotted';
export interface Anchor { t: number; p: number }
export interface Style {
  color: string; // #rrggbb
  width: number; // 1..4
  dash: Dash;
  extendLeft?: boolean;
  extendRight?: boolean;
}
export interface Drawing {
  id: string;
  tool: ToolId;
  points: Anchor[];
  style: Style;
  text?: string;
  locked: boolean;
  hidden: boolean;
}
export interface Doc { version: 1; symbol: string; drawings: Drawing[] }

export const DEFAULT_STYLE: Style = { color: '#2962ff', width: 2, dash: 'solid' };
export const COLORS = ['#2962ff', '#f23645', '#089981', '#ff9800', '#9c27b0', '#e0a43a', '#d5dde6', '#787b86'];

export class DrawingError extends Error {}

let counter = 0;
function freshId(): string {
  counter += 1;
  return `d${Date.now().toString(36)}${counter.toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

export function newDrawing(toolId: ToolId, points: Anchor[], style: Partial<Style> = {}, text?: string): Drawing {
  const info = TOOLS[toolId];
  if (points.length !== info.anchors) throw new DrawingError(`${info.label} needs ${info.anchors} points`);
  const d: Drawing = { id: freshId(), tool: toolId, points: points.map((a) => ({ ...a })), style: { ...DEFAULT_STYLE, ...style }, locked: false, hidden: false };
  if (text !== undefined) d.text = text;
  return d;
}

// ---- validation -------------------------------------------------------------------------------------------------------------------------------

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const DASHES: Dash[] = ['solid', 'dashed', 'dotted'];

function validateDrawing(raw: unknown): Drawing {
  if (!isObject(raw)) throw new DrawingError('a drawing is not an object');
  const toolId = raw.tool as ToolId;
  if (typeof raw.tool !== 'string' || !(raw.tool in TOOLS)) throw new DrawingError(`unknown tool ${String(raw.tool)}`);
  if (typeof raw.id !== 'string' || !raw.id || raw.id.length > 40) throw new DrawingError('a drawing needs an id');
  if (!Array.isArray(raw.points) || raw.points.length !== TOOLS[toolId].anchors) throw new DrawingError(`${TOOLS[toolId].label} needs ${TOOLS[toolId].anchors} points`);
  const points = raw.points.map((a): Anchor => {
    if (!isObject(a) || typeof a.t !== 'number' || typeof a.p !== 'number' || !Number.isFinite(a.t) || !Number.isFinite(a.p)) throw new DrawingError('a point is not a time and a price');
    return { t: a.t, p: a.p };
  });
  const s = isObject(raw.style) ? raw.style : {};
  if (typeof s.color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(s.color)) throw new DrawingError('colour must be #rrggbb');
  if (typeof s.width !== 'number' || !Number.isInteger(s.width) || s.width < 1 || s.width > 4) throw new DrawingError('width must be 1 to 4');
  if (!DASHES.includes(s.dash as Dash)) throw new DrawingError('line style must be solid, dashed or dotted');
  const style: Style = { color: s.color, width: s.width, dash: s.dash as Dash };
  if (s.extendLeft === true) style.extendLeft = true;
  if (s.extendRight === true) style.extendRight = true;
  if (raw.text !== undefined && (typeof raw.text !== 'string' || raw.text.length > MAX_TEXT)) throw new DrawingError(`text is at most ${MAX_TEXT} characters`);
  const d: Drawing = { id: raw.id, tool: toolId, points, style, locked: raw.locked === true, hidden: raw.hidden === true };
  if (typeof raw.text === 'string') d.text = raw.text;
  return d;
}

/** Read a saved document. Anything of the wrong shape is refused with a reason (the caller keeps what it had). */
export function validateDoc(raw: unknown, symbol?: string): Doc {
  if (!isObject(raw) || raw.version !== 1) throw new DrawingError('not a drawings document');
  if (typeof raw.symbol !== 'string') throw new DrawingError('the document has no symbol');
  if (symbol !== undefined && raw.symbol.toUpperCase() !== symbol.toUpperCase()) throw new DrawingError('the document is for another symbol');
  if (!Array.isArray(raw.drawings)) throw new DrawingError('drawings must be a list');
  if (raw.drawings.length > MAX_DRAWINGS) throw new DrawingError(`at most ${MAX_DRAWINGS} drawings per symbol`);
  const drawings = raw.drawings.map(validateDrawing);
  if (new Set(drawings.map((d) => d.id)).size !== drawings.length) throw new DrawingError('two drawings share an id');
  return { version: 1, symbol: raw.symbol, drawings };
}

export const serializeDoc = (symbol: string, drawings: Drawing[]): string => JSON.stringify({ version: 1, symbol, drawings } satisfies Doc);

// ---- list operations (each returns a new list) --------------------------------------------------------------------------------------------------

type Patch = Partial<{ style: Partial<Style>; text: string; locked: boolean; hidden: boolean }>;

const map = (list: Drawing[], id: string, f: (d: Drawing) => Drawing): Drawing[] => {
  let hit = false;
  const next = list.map((d) => {
    if (d.id !== id) return d;
    hit = true;
    return f(d);
  });
  return hit ? next : list;
};

export function add(list: Drawing[], d: Drawing): Drawing[] {
  if (list.length >= MAX_DRAWINGS) throw new DrawingError(`at most ${MAX_DRAWINGS} drawings per symbol`);
  return [...list, d];
}

export const remove = (list: Drawing[], id: string): Drawing[] => list.filter((d) => d.id !== id);
export const removeAll = (): Drawing[] => [];

export function update(list: Drawing[], id: string, patch: Patch): Drawing[] {
  return map(list, id, (d) => {
    const next: Drawing = { ...d, style: { ...d.style, ...patch.style } };
    if (patch.style) {
      if (patch.style.extendLeft === false) delete next.style.extendLeft;
      if (patch.style.extendRight === false) delete next.style.extendRight;
    }
    if (patch.text !== undefined) next.text = patch.text.slice(0, MAX_TEXT);
    if (patch.locked !== undefined) next.locked = patch.locked;
    if (patch.hidden !== undefined) next.hidden = patch.hidden;
    return next;
  });
}

/** Move a whole drawing by a time and a price. A locked drawing does not move. */
export function moveBy(list: Drawing[], id: string, dt: number, dp: number): Drawing[] {
  return map(list, id, (d) => (d.locked ? d : { ...d, points: d.points.map((a) => ({ t: a.t + dt, p: a.p + dp })) }));
}

/** Put one anchor somewhere else. A locked drawing does not change. */
export function moveHandle(list: Drawing[], id: string, index: number, to: Anchor): Drawing[] {
  return map(list, id, (d) => (d.locked || index < 0 || index >= d.points.length ? d : { ...d, points: d.points.map((a, i) => (i === index ? { t: to.t, p: to.p } : a)) }));
}

/** A copy on top, shifted by `dt` and `dp` so it can be told from the original. */
export function duplicate(list: Drawing[], id: string, dt: number, dp: number): { list: Drawing[]; id: string | null } {
  const src = list.find((d) => d.id === id);
  if (!src || list.length >= MAX_DRAWINGS) return { list, id: null };
  const copy: Drawing = { ...src, id: freshId(), locked: false, points: src.points.map((a) => ({ t: a.t + dt, p: a.p + dp })), style: { ...src.style } };
  return { list: [...list, copy], id: copy.id };
}

export function reorder(list: Drawing[], id: string, to: 'front' | 'back'): Drawing[] {
  const d = list.find((x) => x.id === id);
  if (!d) return list;
  const rest = list.filter((x) => x.id !== id);
  return to === 'front' ? [...rest, d] : [d, ...rest];
}

export const setAllLocked = (list: Drawing[], locked: boolean): Drawing[] => (list.every((d) => d.locked === locked) ? list : list.map((d) => ({ ...d, locked })));
export const setAllHidden = (list: Drawing[], hidden: boolean): Drawing[] => (list.every((d) => d.hidden === hidden) ? list : list.map((d) => ({ ...d, hidden })));
