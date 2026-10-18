import { el } from '../../panels/base';
import type { DrawingController } from './controller';
import { TOOLS } from './model';

const when = (t: number): string => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, t % 86_400 === 0 ? 10 : 16);

/** The Objects tab: every drawing of the symbol, with show/hide, lock and delete, and a click to select it. */
export function buildObjects(c: DrawingController): HTMLElement {
  const root = el('div', 'lab-objects');
  const paint = (): void => {
    root.replaceChildren();
    if (!c.list.length) {
      root.append(el('div', 'note', 'No drawings on this symbol. Pick a tool on the left and click on the chart.'));
      return;
    }
    for (const d of [...c.list].reverse()) {
      const row = el('div', `obj-row${d.id === c.selectedId ? ' on' : ''}${d.hidden ? ' off' : ''}`);
      row.dataset.id = d.id;
      row.dataset.tool = d.tool;
      const name = el('button', 'obj-name', TOOLS[d.tool].label);
      name.type = 'button';
      name.append(el('span', 'note', ` ${d.points[0].p.toFixed(2)} · ${when(d.points[0].t)}`));
      name.addEventListener('click', () => c.select(d.id));
      const eye = el('button', 'obj-btn', d.hidden ? '○' : '◉');
      eye.type = 'button';
      eye.title = d.hidden ? 'Show' : 'Hide';
      eye.setAttribute('aria-label', `${d.hidden ? 'show' : 'hide'} ${TOOLS[d.tool].label}`);
      eye.addEventListener('click', () => c.change({ hidden: !d.hidden }, d.id));
      const lock = el('button', `obj-btn${d.locked ? ' on' : ''}`, 'Lk');
      lock.type = 'button';
      lock.title = d.locked ? 'Unlock' : 'Lock';
      lock.setAttribute('aria-label', `${d.locked ? 'unlock' : 'lock'} ${TOOLS[d.tool].label}`);
      lock.addEventListener('click', () => c.change({ locked: !d.locked }, d.id));
      const del = el('button', 'obj-btn', '×');
      del.type = 'button';
      del.title = 'Delete';
      del.disabled = d.locked;
      del.setAttribute('aria-label', `delete ${TOOLS[d.tool].label}`);
      del.addEventListener('click', () => c.deleteById(d.id));
      row.append(eye, name, lock, del);
      root.append(row);
    }
  };
  c.subscribe(paint);
  paint();
  return root;
}
