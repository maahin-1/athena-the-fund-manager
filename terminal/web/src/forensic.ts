import { el } from './panels/base';
import { concernText, plainName, readJobStatus, runningText, startProblem, termText, type JobStatus, type ReportDoc } from './forensiclogic';

const POLL_MS = 3000;

export type Schedule = (fn: () => void, ms: number) => unknown;

/** The report of the Analyse button: starts the job for one draft, follows it, and shows what was checked, what differs (with the pages and the possible innocent explanations), what could not be
 *  run and why, and the limits. Display only; every piece of text reaches the page as text. */
export class ForensicPanel {
  readonly element = el('div', 'forensic');
  private token = 0;

  constructor(
    private fetcher: typeof fetch = (...a) => fetch(...a),
    private schedule: Schedule = (fn, ms) => setTimeout(fn, ms),
  ) {
    this.element.hidden = true;
  }

  /** Start the job for a draft and follow it until it ends. */
  async analyse(url: string, company: string, kind: string): Promise<void> {
    const mine = ++this.token;
    company = plainName(company, 'Unnamed company');
    kind = plainName(kind, 'DRHP');
    this.element.hidden = false;
    this.show(company, kind, [el('div', 'note', 'Starting…')]);
    try {
      const r = await this.fetcher('/api/forensic', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ url, company, kind }) });
      if (mine !== this.token) return;
      if (!r.ok) {
        this.show(company, kind, [el('div', 'note', startProblem(r.status))]);
        return;
      }
    } catch {
      if (mine === this.token) this.show(company, kind, [el('div', 'note', startProblem(502))]);
      return;
    }
    await this.poll(mine, url, company, kind);
  }

  close(): void {
    this.token++;
    this.element.hidden = true;
    this.element.replaceChildren();
  }

  private async poll(mine: number, url: string, company: string, kind: string): Promise<void> {
    if (mine !== this.token) return;
    let status: JobStatus | null = null;
    try {
      const r = await this.fetcher('/api/forensic');
      status = r.ok ? readJobStatus(await r.json()) : null;
    } catch {
      status = null;
    }
    if (mine !== this.token) return;
    if (!status) {
      this.show(company, kind, [el('div', 'note', 'The analysis did not answer; try again in a minute.')]);
      return;
    }
    if (status.state === 'running' && status.url === url) {
      this.show(company, kind, [el('div', 'note', runningText(status.seconds))]);
      this.schedule(() => void this.poll(mine, url, company, kind), POLL_MS);
      return;
    }
    if (status.state === 'done' && status.url === url) {
      this.show(company, kind, this.report(status.report));
      return;
    }
    if (status.state === 'failed' && status.url === url) {
      this.show(company, kind, [el('div', 'note', status.error)]);
      return;
    }
    this.show(company, kind, [el('div', 'note', status.state === 'running' ? 'Another document is being analysed; wait for it to finish.' : 'The analysis is no longer there; start it again.')]);
  }

  private show(company: string, kind: string, body: HTMLElement[]): void {
    const bar = el('div', 'sent-bar');
    const close = el('button', 'btn', 'Close');
    close.type = 'button';
    close.addEventListener('click', () => this.close());
    bar.append(el('b', 'rtitle', `Checks on ${company} ${kind}`), close);
    this.element.replaceChildren(bar, ...body);
  }

  private report(r: ReportDoc): HTMLElement[] {
    const out: HTMLElement[] = [];
    out.push(el('div', 'sent-section', concernText(r)));
    if (r.narrative) out.push(el('div', 'note', r.narrative));
    out.push(el('div', 'note', `Read: ${r.pages} pages, ${r.sectionsFound} sections found${r.sectionsNotFound.length ? `; not found: ${r.sectionsNotFound.join(', ')}` : ''}${r.pagesWithoutText ? `; ${r.pagesWithoutText} pages without text` : ''}. Checked ${r.generatedAt.slice(0, 16).replace('T', ' ')} UTC, ${r.modelCalls} model calls.`));
    out.push(el('div', 'sent-section', `Checks: ${r.held} held, ${r.differ} differ, ${r.notRunCount} could not be run`));
    const table = el('table', 'stable');
    const head = el('tr');
    for (const h of ['What was compared', 'Held', 'Differ', 'Not run']) head.append(el('th', '', h));
    table.append(head);
    for (const g of r.groups) {
      const tr = el('tr');
      tr.append(el('td', '', g.title), el('td', '', String(g.held)), el('td', g.differ ? 'dn' : '', String(g.differ)), el('td', '', String(g.notRun)));
      table.append(tr);
    }
    out.push(table);
    if (r.differences.length) {
      out.push(el('div', 'sent-section', `Differences to look at (${r.differences.length})`));
      for (const d of r.differences) {
        const box = el('div', 'forensic-diff');
        box.append(el('b', '', d.title), el('div', 'note', d.reason), el('div', 'note', `Left: ${d.left.map(termText).join('; ')}`), el('div', 'note', `Right: ${d.right.map(termText).join('; ')}`));
        if (d.pages.length) box.append(el('div', 'note', `PDF page ${d.pages.join(', ')}`));
        out.push(box);
      }
      for (const f of r.flags) {
        const box = el('div', 'forensic-flag');
        box.append(el('b', '', `${f.id} (${f.status.replace('_', ' ')})`), el('div', 'note', f.text));
        if (f.innocent.length) box.append(el('div', 'note', `Possible innocent explanations: ${f.innocent.join('; ')}`));
        out.push(box);
      }
    } else out.push(el('div', 'note', 'No difference was found in what could be checked. That is not a clearance: see what could not be run, and the limits below.'));
    if (r.notRun.length) {
      out.push(el('div', 'sent-section', 'Could not be run'));
      for (const n of r.notRun) out.push(el('div', 'note', `${n.count} × ${n.reason}`));
    }
    if (r.notExamined.length) out.push(el('div', 'note', `Not examined: ${r.notExamined.map((n) => `${n.lane} (${n.why})`).join('; ')}.`));
    if (r.notes.length) out.push(el('div', 'note', r.notes.join(' ')));
    if (r.notBuilt.length) out.push(el('div', 'note', `Not built yet: ${r.notBuilt.join('; ')}.`));
    out.push(el('div', 'sent-section', 'Limits'));
    for (const l of r.limits) out.push(el('div', 'note', l));
    out.push(el('div', 'note sent-caveat', r.caveat));
    return out;
  }
}
