import { el } from '../panels/base';
import type { IndicatorInstance } from './model';
import { defaults, type IndicatorDef, type Params } from './registry';

function hexOf(color: string): string {
  return /^#[0-9a-fA-F]{6}$/.test(color) ? color : '#888888';
}

function themeColor(variable: string): string {
  return hexOf(getComputedStyle(document.documentElement).getPropertyValue(variable).trim());
}

/** The settings dialog of one indicator: its inputs and a colour for each thing it draws. `apply` returns an error text to keep the dialog open,
 *  or null when the change was taken. Returns the dialog's element (already attached to `parent`). */
export function settingsDialog(
  parent: HTMLElement, def: IndicatorDef, inst: IndicatorInstance, apply: (params: Params, colors: Record<string, string>) => string | null,
): HTMLElement {
  const overlay = el('div', 'lab-modal');
  const box = el('div', 'lab-dialog');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', `${def.name} settings`);
  box.append(el('h3', '', def.name));
  const fields = new Map<string, HTMLInputElement | HTMLSelectElement>();
  const colors = new Map<string, HTMLInputElement>();
  const form = el('div', 'lab-form');
  for (const input of def.inputs) {
    const row = el('label', 'lab-row');
    row.append(el('span', '', input.label));
    let field: HTMLInputElement | HTMLSelectElement;
    if (input.kind === 'select') {
      field = el('select');
      for (const o of input.options ?? []) field.append(new Option(o, o));
    } else {
      field = el('input');
      field.type = 'number';
      field.step = input.kind === 'int' ? '1' : 'any';
      if (input.min !== undefined) field.min = String(input.min);
      if (input.max !== undefined) field.max = String(input.max);
    }
    field.value = String(inst.params[input.key] ?? input.default);
    field.dataset.input = input.key;
    fields.set(input.key, field);
    row.append(field);
    form.append(row);
  }
  for (const out of def.outputs) {
    const row = el('label', 'lab-row');
    row.append(el('span', '', `${out.label} colour`));
    const picker = el('input');
    picker.type = 'color';
    picker.value = hexOf(inst.colors[out.key] ?? themeColor(out.color));
    picker.dataset.color = out.key;
    colors.set(out.key, picker);
    row.append(picker);
    form.append(row);
  }
  const message = el('div', 'note bad');
  const close = () => overlay.remove();
  const ok = el('button', 'btn', 'OK');
  ok.addEventListener('click', () => {
    const params: Params = {};
    for (const [key, field] of fields) params[key] = def.inputs.find((i) => i.key === key)?.kind === 'select' ? field.value : field.value === '' ? NaN : Number(field.value);
    const chosen: Record<string, string> = {};
    for (const [key, picker] of colors) {
      const original = hexOf(inst.colors[key] ?? themeColor(def.outputs.find((o) => o.key === key)?.color ?? ''));
      if (picker.value !== original) chosen[key] = picker.value; // only a colour the user changed becomes an override
    }
    const error = apply(params, chosen);
    if (error) message.textContent = error;
    else close();
  });
  const reset = el('button', 'btn', 'Defaults');
  reset.addEventListener('click', () => {
    for (const [key, value] of Object.entries(defaults(def))) {
      const field = fields.get(key);
      if (field) field.value = String(value);
    }
    message.textContent = '';
  });
  const cancel = el('button', 'btn', 'Cancel');
  cancel.addEventListener('click', close);
  overlay.addEventListener('keydown', (e) => e.key === 'Escape' && close());
  overlay.addEventListener('click', (e) => e.target === overlay && close());
  const actions = el('div', 'lab-actions');
  actions.append(reset, cancel, ok);
  box.append(form, message, actions);
  overlay.append(box);
  parent.append(overlay);
  return overlay;
}
