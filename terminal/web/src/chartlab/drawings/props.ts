import { el } from '../../panels/base';
import type { DrawingController } from './controller';
import { COLORS, TOOLS, type Dash, type ToolId } from './model';

const EXTENDABLE: ToolId[] = ['trend', 'info', 'channel', 'flat', 'fib', 'fibext', 'angle', 'fibchannel', 'trianglepat'];
const WORDS: ToolId[] = ['text', 'label', 'arrowup', 'arrowdown'];

/** The bar over the chart for the selected drawing: colour, width, line style, extend left and right, words, lock, duplicate, order and delete. Hidden when nothing is selected. */
export function buildProps(c: DrawingController): HTMLElement {
  const root = el('div', 'lab-props');
  root.hidden = true;
  root.setAttribute('role', 'toolbar');
  root.setAttribute('aria-label', 'drawing properties');
  const typing = el('input', 'cmd props-text');
  typing.setAttribute('aria-label', 'drawing text');
  typing.maxLength = 200;
  typing.addEventListener('keydown', (e) => e.stopPropagation());
  typing.addEventListener('change', () => c.change({ text: typing.value }));
  const width = el('select', 'lab-select props-width');
  width.setAttribute('aria-label', 'drawing width');
  for (const w of [1, 2, 3, 4]) width.append(new Option(`${w}px`, String(w)));
  width.addEventListener('change', () => c.change({ style: { width: Number(width.value) } }));
  const dash = el('select', 'lab-select props-dash');
  dash.setAttribute('aria-label', 'drawing line style');
  for (const d of ['solid', 'dashed', 'dotted'] as Dash[]) dash.append(new Option(d, d));
  dash.addEventListener('change', () => c.change({ style: { dash: dash.value as Dash } }));
  const swatches = el('span', 'props-colors');
  for (const color of COLORS) {
    const s = el('button', 'swatch');
    s.type = 'button';
    s.dataset.color = color;
    s.style.background = color;
    s.title = color;
    s.setAttribute('aria-label', `drawing colour ${color}`);
    s.addEventListener('click', () => c.change({ style: { color } }));
    swatches.append(s);
  }
  const toggle = (label: string, aria: string, title: string, click: () => void): HTMLButtonElement => {
    const b = el('button', 'btn props-btn', label);
    b.type = 'button';
    b.title = title;
    b.setAttribute('aria-label', aria);
    b.addEventListener('click', click);
    return b;
  };
  const extL = toggle('◂', 'extend left', 'Continue the line to the left', () => c.change({ style: { extendLeft: !(c.selected?.style.extendLeft ?? false) } }));
  const extR = toggle('▸', 'extend right', 'Continue the line to the right', () => c.change({ style: { extendRight: !(c.selected?.style.extendRight ?? false) } }));
  const lock = toggle('Lock', 'lock drawing', 'Lock or unlock this drawing', () => c.change({ locked: !(c.selected?.locked ?? false) }));
  const copy = toggle('Copy', 'duplicate drawing', 'Duplicate this drawing', () => c.duplicateSelected());
  const front = toggle('Front', 'bring to front', 'Bring to the front', () => c.reorderSelected('front'));
  const back = toggle('Back', 'send to back', 'Send to the back', () => c.reorderSelected('back'));
  const del = toggle('Delete', 'delete drawing', 'Delete this drawing (Delete key)', () => c.deleteSelected());
  const name = el('span', 'props-name');
  root.append(name, swatches, width, dash, extL, extR, typing, lock, copy, front, back, del);

  const paint = (): void => {
    const d = c.selected;
    root.hidden = d === null;
    if (!d) return;
    name.textContent = TOOLS[d.tool].label;
    width.value = String(d.style.width);
    dash.value = d.style.dash;
    swatches.querySelectorAll<HTMLElement>('.swatch').forEach((s) => s.classList.toggle('on', s.dataset.color?.toLowerCase() === d.style.color.toLowerCase()));
    extL.hidden = extR.hidden = !EXTENDABLE.includes(d.tool);
    extL.classList.toggle('on', d.style.extendLeft === true);
    extR.classList.toggle('on', d.style.extendRight === true);
    typing.hidden = !WORDS.includes(d.tool);
    if (document.activeElement !== typing) typing.value = d.text ?? '';
    lock.classList.toggle('on', d.locked);
    lock.textContent = d.locked ? 'Unlock' : 'Lock';
    del.disabled = d.locked;
  };
  c.subscribe(paint);
  paint();
  return root;
}
