import { el } from '../../panels/base';
import type { DrawingController } from './controller';
import { GROUPS, TOOL_IDS, TOOLS, type Group } from './model';
import type { Magnet } from './projection';

const GLYPH: Record<Group, string> = { lines: '╱', channels: '∥', fib: 'Fib', shapes: '▭', notes: 'T', measure: '↔', position: '⇅' };
const NEXT_MAGNET: Record<Magnet, Magnet> = { off: 'weak', weak: 'strong', strong: 'off' };
const MAGNET_TEXT: Record<Magnet, string> = { off: 'Magnet: off', weak: 'Magnet: weak (snaps to open, high, low or close when near)', strong: 'Magnet: strong (always snaps to open, high, low or close)' };

export interface Rail {
  element: HTMLElement;
  /** Switch the rail on or off (off for charts with no time axis, with the reason as its tooltip). */
  setEnabled(on: boolean, why?: string): void;
}

/** The tool rail left of the chart: cursor, a button per tool group with a fly-out of its tools, magnet, keep drawing, lock all, hide all, remove all. */
export function buildRail(c: DrawingController): Rail {
  const root = el('div', 'lab-rail');
  root.setAttribute('role', 'toolbar');
  root.setAttribute('aria-label', 'drawing tools');
  let open: Group | null = null;
  let removeArmed = false;
  let enabled = true;

  const button = (text: string, label: string, title: string, click: () => void, cls = 'rail-btn'): HTMLButtonElement => {
    const b = el('button', cls, text);
    b.type = 'button';
    b.title = title;
    b.setAttribute('aria-label', label);
    b.addEventListener('click', click);
    return b;
  };

  const cursor = button('➤', 'cursor', 'Cursor: select, move and edit drawings', () => {
    open = null;
    c.chooseTool(null);
  });
  const groups = new Map<Group, { wrap: HTMLElement; btn: HTMLButtonElement; fly: HTMLElement }>();
  for (const g of GROUPS) {
    const wrap = el('div', 'rail-group');
    const btn = button(GLYPH[g.id], `tool group ${g.label}`, g.label, () => {
      open = open === g.id ? null : g.id;
      paint();
    });
    const fly = el('div', 'rail-fly');
    fly.hidden = true;
    for (const id of TOOL_IDS.filter((t) => TOOLS[t].group === g.id)) {
      const t = el('button', 'rail-tool', TOOLS[id].label);
      t.type = 'button';
      t.dataset.tool = id;
      t.addEventListener('click', () => {
        open = null;
        c.chooseTool(id);
      });
      fly.append(t);
    }
    wrap.append(btn, fly);
    groups.set(g.id, { wrap, btn, fly });
  }
  const magnet = button('Mg', 'magnet', MAGNET_TEXT.off, () => c.setMagnet(NEXT_MAGNET[c.magnet]));
  const keep = button('Keep', 'keep drawing', 'Keep the chosen tool after each drawing (Escape ends it)', () => c.setKeepDrawing(!c.keepDrawing));
  const lock = button('Lock', 'lock all drawings', 'Lock every drawing, or unlock them all', () => c.lockAll(!c.list.every((d) => d.locked)));
  const hide = button('Hide', 'hide all drawings', 'Hide every drawing, or show them all', () => c.hideAll(!c.list.every((d) => d.hidden)));
  const clear = button('Clear', 'remove all drawings', 'Remove every drawing of this symbol (asks first)', () => {
    if (!removeArmed) {
      removeArmed = true;
      paint();
      setTimeout(() => {
        if (removeArmed) {
          removeArmed = false;
          paint();
        }
      }, 3000);
      return;
    }
    removeArmed = false;
    c.removeEverything();
  });
  root.append(cursor, ...[...groups.values()].map((g) => g.wrap), el('hr', 'rail-sep'), magnet, keep, lock, hide, clear);

  function paint(): void {
    cursor.classList.toggle('on', c.tool === null);
    for (const [id, g] of groups) {
      const chosen = c.tool && TOOLS[c.tool].group === id;
      g.btn.classList.toggle('on', !!chosen);
      g.fly.hidden = open !== id;
      g.fly.querySelectorAll<HTMLElement>('.rail-tool').forEach((t) => t.classList.toggle('on', t.dataset.tool === c.tool));
    }
    magnet.dataset.mode = c.magnet;
    magnet.classList.toggle('on', c.magnet !== 'off');
    magnet.title = MAGNET_TEXT[c.magnet];
    keep.classList.toggle('on', c.keepDrawing);
    const all = (f: (d: (typeof c.list)[number]) => boolean) => c.list.length > 0 && c.list.every(f);
    lock.classList.toggle('on', all((d) => d.locked));
    hide.classList.toggle('on', all((d) => d.hidden));
    clear.textContent = removeArmed ? `Sure? ${c.list.length}` : 'Clear';
    clear.classList.toggle('armed', removeArmed);
    clear.disabled = c.list.length === 0;
    for (const b of root.querySelectorAll('button')) {
      if (!enabled) (b as HTMLButtonElement).disabled = true;
    }
    if (enabled) clear.disabled = c.list.length === 0;
  }

  c.subscribe(paint);
  document.addEventListener('pointerdown', (e) => {
    if (open && !root.contains(e.target as Node)) {
      open = null;
      paint();
    }
  });
  paint();

  return {
    element: root,
    setEnabled(on, why = '') {
      enabled = on;
      root.classList.toggle('off', !on);
      root.title = on ? '' : why;
      if (on) for (const b of root.querySelectorAll('button')) (b as HTMLButtonElement).disabled = false;
      paint();
    },
  };
}
