import { Notebook, serverNotebookIO } from '../notes/store';
import { NoteError, search, symbols, tags, toMarkdown, MAX_TEXT, type Note } from '../notes/model';
import { el, Panel, type AppContext } from './base';

/** One notebook for the whole page, shared by every Notes panel. */
let shared: Notebook | null = null;
export const notebook = (): Notebook => (shared ??= new Notebook(serverNotebookIO()));
/** For tests: a notebook of the test's own. */
export const useNotebook = (n: Notebook | null): void => {
  shared = n;
};

const stamp = (ms: number): string => new Date(ms).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });

/** Notes on the symbol the panel follows, or every note: write, edit, delete, search by text or tag, and export the lot as Markdown. Kept on this machine only. */
export class NotesPanel extends Panel {
  private readonly book = notebook();
  private all = false;
  private editing: string | null = null;
  private readonly status = el('div', 'note');
  private readonly modes = el('div', 'notes-modes');
  private readonly find = el('input', 'cmd notes-find');
  private readonly tagBox = el('select', 'picker notes-tag');
  private readonly form = el('div', 'notes-form');
  private readonly text = el('textarea', 'notes-text');
  private readonly tagInput = el('input', 'cmd notes-taginput');
  private readonly list = el('div', 'notes-list');
  private off: (() => void) | null = null;

  constructor(id: string, ctx: AppContext) {
    super(id, ctx);
    this.element.classList.add('notes');
    this.start();
  }

  protected build(): void {
    this.setTitle('Notes');
    for (const [all, label] of [[false, 'This symbol'], [true, 'All notes']] as const) {
      const b = el('button', 'btn', label);
      b.type = 'button';
      b.dataset.scope = all ? 'all' : 'symbol';
      b.addEventListener('click', () => {
        this.all = all;
        this.paint();
      });
      this.modes.append(b);
    }
    const exportBtn = el('button', 'btn', 'Export');
    exportBtn.type = 'button';
    exportBtn.title = 'Download every note as a Markdown file';
    exportBtn.addEventListener('click', () => this.exportAll());
    this.modes.append(exportBtn);
    this.find.placeholder = 'search notes';
    this.find.setAttribute('aria-label', 'search notes');
    this.find.addEventListener('input', () => this.paintList());
    this.find.addEventListener('keydown', (e) => e.stopPropagation());
    this.tagBox.setAttribute('aria-label', 'filter by tag');
    this.tagBox.addEventListener('change', () => this.paintList());
    this.text.setAttribute('aria-label', 'note text');
    this.text.placeholder = 'a note for this symbol';
    this.text.maxLength = MAX_TEXT;
    this.text.rows = 3;
    this.text.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) this.submit();
    });
    this.tagInput.setAttribute('aria-label', 'note tag');
    this.tagInput.placeholder = 'tag';
    this.tagInput.addEventListener('keydown', (e) => e.stopPropagation());
    const save = el('button', 'btn', 'Add note');
    save.type = 'button';
    save.dataset.action = 'save';
    save.addEventListener('click', () => this.submit());
    const cancel = el('button', 'btn', 'Cancel');
    cancel.type = 'button';
    cancel.dataset.action = 'cancel';
    cancel.addEventListener('click', () => this.resetForm());
    this.form.append(this.text, this.tagInput, save, cancel);
    const bar = el('div', 'notes-bar');
    bar.append(this.find, this.tagBox);
    this.body.append(this.status, this.modes, bar, this.form, this.list);
    this.off = this.book.subscribe(() => this.paint());
    void this.book.load();
    this.paint();
  }

  protected symbolChanged(): void {
    this.editing = null;
    this.paint();
  }

  private submit(): void {
    try {
      if (this.editing) this.book.edit(this.editing, this.text.value, this.tagInput.value);
      else if (this.symbol) this.book.add(this.symbol, this.text.value, this.tagInput.value);
      else throw new NoteError('pick a symbol first');
      this.resetForm();
      this.status.textContent = '';
    } catch (e) {
      this.status.textContent = e instanceof NoteError ? e.message : 'The note could not be added.';
    }
  }

  private resetForm(): void {
    this.editing = null;
    this.text.value = '';
    this.tagInput.value = '';
    this.paint();
  }

  private exportAll(): void {
    const blob = new Blob([toMarkdown(this.book.notes)], { type: 'text/markdown' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `notebook-${new Date().toISOString().slice(0, 10)}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  private paint(): void {
    this.modes.querySelectorAll<HTMLButtonElement>('button[data-scope]').forEach((b) => b.classList.toggle('on', (b.dataset.scope === 'all') === this.all));
    const save = this.form.querySelector<HTMLButtonElement>('button[data-action="save"]');
    if (save) save.textContent = this.editing ? 'Save changes' : 'Add note';
    this.form.querySelector<HTMLButtonElement>('button[data-action="cancel"]')!.hidden = !this.editing;
    this.text.placeholder = this.symbol ? `a note on ${this.symbol}` : 'pick a symbol to write a note';
    const used = tags(this.book.notes);
    const chosen = this.tagBox.value;
    this.tagBox.replaceChildren(new Option('any tag', ''), ...used.map(([t, n]) => new Option(`${t} (${n})`, t)));
    this.tagBox.value = used.some(([t]) => t === chosen) ? chosen : '';
    if (this.book.problem) this.status.textContent = this.book.problem;
    this.paintList();
  }

  private paintList(): void {
    const found = search(this.book.notes, { symbol: this.all ? undefined : this.symbol || '@', tag: this.tagBox.value || undefined, text: this.find.value });
    this.list.replaceChildren();
    if (found.length === 0) {
      this.list.append(el('div', 'note', this.book.notes.length === 0 ? 'No notes yet. They are kept on this machine, in the terminal\'s own folder.' : this.all ? 'No note matches.' : `No note on ${this.symbol || 'this symbol'} matches.`));
      return;
    }
    if (this.all) this.list.append(el('div', 'note', symbols(this.book.notes).map(([s, n]) => `${s} ${n}`).join(' · ')));
    for (const n of found) this.list.append(this.item(n));
  }

  private item(n: Note): HTMLElement {
    const box = el('div', 'note-item');
    box.dataset.id = n.id;
    const head = el('div', 'note-head');
    const sym = el('button', 'note-symbol', n.symbol);
    sym.type = 'button';
    sym.title = `Show ${n.symbol}`;
    sym.addEventListener('click', () => this.pick(n.symbol));
    head.append(sym, el('span', 'note-time', n.updated !== n.created ? `${stamp(n.created)} (edited ${stamp(n.updated)})` : stamp(n.created)));
    if (n.tag) head.append(el('span', 'chip flat', n.tag));
    const edit = el('button', 'btn', 'Edit');
    edit.type = 'button';
    edit.setAttribute('aria-label', `edit note on ${n.symbol}`);
    edit.addEventListener('click', () => {
      this.editing = n.id;
      this.text.value = n.text;
      this.tagInput.value = n.tag;
      this.paint();
      this.text.focus();
    });
    const del = el('button', 'btn', 'Delete');
    del.type = 'button';
    del.setAttribute('aria-label', `delete note on ${n.symbol}`);
    del.addEventListener('click', () => this.book.remove(n.id));
    head.append(edit, del);
    box.append(head, el('div', 'note-body', n.text));
    return box;
  }

  dispose(): void {
    this.off?.();
    super.dispose();
  }
}
