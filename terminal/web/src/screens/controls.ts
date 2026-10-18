import { el } from '../panels/base';
import { COLUMNS } from '../screenlogic';
import { FIELDS, parseQuery, type Node } from '../screenquery';
import { ScreensError, type SavedScreen, type SavedScreens } from './store';

/** The query box of the Screen tab: a line of text parsed as it is typed, an error line that says what and where, and a list of the fields with an
 *  example each (a click puts the example in the box). While the text does not parse the table keeps the last query that did. */
export class QueryBar {
  readonly element = el('div', 'squerybar');
  /** The last query that parsed (null for an empty one, which matches every row). */
  node: Node | null = null;
  private readonly input = el('input', 'cmd squery');
  private readonly error = el('div', 'note qerror');

  constructor(private onChange: () => void) {
    this.input.placeholder = 'query, for example  pe < 20 and roe > 15 and index = "NIFTY 500"';
    this.input.setAttribute('aria-label', 'screen query');
    this.input.spellcheck = false;
    this.input.autocomplete = 'off';
    this.input.addEventListener('input', () => this.parse(true));
    this.error.setAttribute('role', 'alert');
    this.element.append(this.input, this.error, this.fieldList());
  }

  get text(): string {
    return this.input.value;
  }

  /** Put a query in the box (without telling the table: the caller repaints). */
  set text(value: string) {
    this.input.value = value;
    this.parse(false);
  }

  private parse(notify: boolean): void {
    const parsed = parseQuery(this.input.value);
    if (parsed.ok) {
      this.node = parsed.node;
      this.error.textContent = '';
      this.input.classList.remove('bad');
      this.input.removeAttribute('aria-invalid');
    } else {
      this.error.textContent = `${parsed.error.message} (at character ${parsed.error.at + 1})`;
      this.input.classList.add('bad');
      this.input.setAttribute('aria-invalid', 'true');
      return; // the table keeps the last query that parsed
    }
    if (notify) this.onChange();
  }

  private fieldList(): HTMLElement {
    const box = el('details', 'sfieldlist');
    box.append(el('summary', '', 'Fields and operators'));
    box.append(
      el('div', 'note', 'Compare with < <= > >= = !=, or: between 5 and 15 · in ("a", "b") · is null · contains "text". Join with and, or, not and brackets. Percentages are written in percent (roe > 15). A stock with no figure in a field meets no limit.'),
    );
    const table = el('table', 'sfields');
    for (const f of FIELDS) {
      const tr = el('tr');
      tr.append(el('td', 'fname', f.names.join(' / ')), el('td', '', `${f.label}${f.unit ? ` (${f.unit})` : ''}`));
      const use = el('button', 'btn', f.example);
      use.type = 'button';
      use.addEventListener('click', () => {
        this.input.value = this.input.value.trim() ? `${this.input.value.trim()} and ${f.example}` : f.example;
        this.parse(true);
        this.input.focus();
      });
      const cell = el('td');
      cell.append(use);
      tr.append(cell);
      table.append(tr);
    }
    box.append(table);
    return box;
  }
}

/** Saved screens: pick one to open it, type a name and Save to keep the screen as it is now (the same name replaces), Delete to remove the picked one. */
export class SavedBar {
  readonly element = el('div', 'ssaved');
  private readonly pick = el('select', 'picker');
  private readonly name = el('input', 'cmd sname');
  private readonly message = el('span', 'note');

  constructor(private saved: SavedScreens, private snapshot: (name: string) => SavedScreen, private apply: (screen: SavedScreen) => void) {
    this.name.placeholder = 'name this screen';
    this.name.maxLength = 60;
    this.name.setAttribute('aria-label', 'screen name');
    this.pick.setAttribute('aria-label', 'saved screens');
    const save = el('button', 'btn', 'Save screen');
    save.type = 'button';
    const del = el('button', 'btn', 'Delete');
    del.type = 'button';
    this.pick.addEventListener('change', () => {
      const screen = this.saved.get(this.pick.value);
      if (!screen) return;
      this.name.value = screen.name;
      this.apply(screen);
      this.say(`Opened "${screen.name}".`);
    });
    save.addEventListener('click', () => void this.save());
    del.addEventListener('click', () => void this.remove());
    this.element.append(this.pick, this.name, save, del, this.message);
    this.refresh();
  }

  refresh(): void {
    this.pick.replaceChildren(new Option(`Saved screens (${this.saved.names().length})`, ''), ...this.saved.names().map((n) => new Option(n, n)));
    if (this.saved.problem) this.say(this.saved.problem);
  }

  private say(text: string): void {
    this.message.textContent = text;
  }

  private async save(): Promise<void> {
    const name = this.name.value.trim();
    if (!name) return this.say('Give the screen a name first.');
    try {
      await this.saved.save(this.snapshot(name));
      this.refresh();
      this.pick.value = name;
      this.say(`Saved "${name}".`);
    } catch (e) {
      this.refresh();
      this.say(e instanceof ScreensError ? e.message : 'Could not save: the server did not answer. The screen is kept until you reload.');
    }
  }

  private async remove(): Promise<void> {
    const name = this.pick.value || this.name.value.trim();
    if (!name || !this.saved.get(name)) return this.say('Pick a saved screen to delete.');
    try {
      await this.saved.remove(name);
      this.name.value = '';
      this.refresh();
      this.say(`Deleted "${name}".`);
    } catch {
      this.refresh();
      this.say('Could not delete: the server did not answer.');
    }
  }
}

/** Which columns the table shows, chosen by checkbox, in the order they were added; at least one stays. */
export class ColumnsMenu {
  readonly element = el('details', 'scols');
  private readonly boxes = new Map<string, HTMLInputElement>();

  constructor(private get: () => string[], private set: (columns: string[]) => void) {
    this.element.append(el('summary', '', 'Columns'));
    const grid = el('div', 'scolgrid');
    for (const [id, [label]] of Object.entries(COLUMNS)) {
      const box = el('input');
      box.type = 'checkbox';
      box.dataset.column = id;
      box.addEventListener('change', () => {
        const now = this.get();
        const next = box.checked ? [...now, id] : now.filter((c) => c !== id);
        if (!next.length) return this.sync(); // a table needs one column
        this.set(next);
      });
      this.boxes.set(id, box);
      const wrap = el('label');
      wrap.append(box, document.createTextNode(` ${label}`));
      grid.append(wrap);
    }
    this.element.append(grid);
  }

  sync(): void {
    const shown = new Set(this.get());
    for (const [id, box] of this.boxes) box.checked = shown.has(id);
  }
}
