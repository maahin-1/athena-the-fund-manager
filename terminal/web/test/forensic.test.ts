// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { ForensicPanel } from '../src/forensic';
import { concernText, plainName, readJobStatus, readReport, runningText, startProblem, termText } from '../src/forensiclogic';
import { IpoView } from '../src/ipo';

const URL = 'https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1_draft.pdf';

/** The shape `athena.forensic.job` writes (see tests/test_forensic_job.py, which pins the same keys). */
const report = (over: Record<string, unknown> = {}) => ({
  schema: 'athena.forensic.report/1', ok: true, mode: 'checks', generated_at: '2026-11-07T09:00:00+00:00',
  document: { doc_id: 'd1', company: 'Deon Energy Limited', kind: 'DRHP', url: URL, sha256: 's', pages: 520, sections: [{ key: 'financials', title: 'Restated financial information', start: 332, end: 386 }], sections_not_found: ['Risk factors'], pages_without_text: 2 },
  concern: { low: 'low', high: 'moderate', score_low: 1, score_high: 2, established: 1, single_source: 0 },
  flags: [{ id: 'L1', status: 'established', type: 'arithmetic', text: 'total assets equal total equity and liabilities: left 305.00 against right 300.00', why: 'proven by the numbers', innocent_explanations: ['a typing error that the final document corrects'], supported_by: 0 }],
  checks: {
    summary: { held: 93, differ: 1, not_run: 14, statement_tables_read: 6 },
    groups: [{ id: 'statements', title: 'Restated statements', held: 42, differ: 1, not_run: 12 }, { id: 'holdings', title: 'Shareholding tables', held: 17, differ: 0, not_run: 0 }],
    differences: [{ id: 'balance_sheet_balances@4:2026', group: 'statements', title: 'total assets equal total equity and liabilities, page 4', reason: 'left 305.00 against right 300.00', pages: [4], printed_pages: [2], left: [{ name: 'total assets', printed: '305.00', page: 4 }], right: [{ name: 'total equity and liabilities', printed: '300.00', page: 4 }], tolerance: 0.01 }],
    not_run: [{ reason: 'not found on the page (or printed more than once)', count: 12 }],
    notes: ['the financial sections were not found'], not_built: ['the management discussion in other forms'],
  },
  not_examined: [{ lane: 'text', why: 'no such agent is available yet' }], limits: ['A check that could not be run is not a clean result.'], narrative: 'Concern level: low to moderate.', caveat: 'Not investment advice.', cost: { model_calls: 0 }, ...over,
});

describe('the report document', () => {
  it('is read defensively: a different schema, a failed job or a missing core part is refused, and bad numbers become zero', () => {
    const got = readReport(report())!;
    expect(got).toMatchObject({ company: 'Deon Energy Limited', pages: 520, sectionsFound: 1, held: 93, differ: 1, notRunCount: 14, established: 1, modelCalls: 0 });
    expect(got.differences[0]).toMatchObject({ pages: [4], printedPages: [2], left: [{ name: 'total assets', printed: '305.00', page: 4 }] });
    expect(got.flags[0].innocent).toEqual(['a typing error that the final document corrects']);
    expect(readReport(report({ schema: 'athena.forensic.report/2' }))).toBeNull();
    expect(readReport({ schema: 'athena.forensic.report/1', ok: false, error: 'x' })).toBeNull();
    expect(readReport(report({ checks: { nonsense: true } }))).toBeNull();
    expect(readReport(null)).toBeNull();
    expect(readReport([1])).toBeNull();
    const odd = readReport(report({ checks: { ...report().checks, summary: { held: 'x', differ: -3, not_run: null }, differences: [{ pages: ['a', 2], left: 'no' }] } }))!;
    expect(odd).toMatchObject({ held: 0, differ: 0, notRunCount: 0 });
    expect(odd.differences[0]).toMatchObject({ pages: [2], left: [] });
  });

  it('reads the status of the job in each of its four states and refuses anything else', () => {
    expect(readJobStatus({ state: 'idle' })).toEqual({ state: 'idle' });
    expect(readJobStatus({ state: 'running', url: URL, seconds: 41 })).toEqual({ state: 'running', url: URL, seconds: 41 });
    expect(readJobStatus({ state: 'failed', url: URL })).toEqual({ state: 'failed', url: URL, error: 'the analysis failed' });
    expect(readJobStatus({ state: 'done', url: URL, report: report() })).toMatchObject({ state: 'done', url: URL });
    expect(readJobStatus({ state: 'done', url: URL, report: { schema: 'other' } })).toBeNull();
    expect(readJobStatus({ state: 'soon' })).toBeNull();
    expect(readJobStatus('idle')).toBeNull();
  });

  it('says the concern as a range with how many differences stand behind it, never as advice, and writes names the server accepts', () => {
    expect(concernText({ concernLow: 'low', concernHigh: 'low', established: 1, singleSource: 0 })).toBe('Concern level: low (1 established difference, 0 with one source only)');
    expect(concernText({ concernLow: 'none established', concernHigh: 'moderate', established: 2, singleSource: 3 })).toBe('Concern level: none established to moderate (2 established differences, 3 with one source only)');
    expect([runningText(41), runningText(125)]).toEqual(['Reading and checking the draft… 41 s so far (a long draft takes a few minutes)', 'Reading and checking the draft… 2 min 5 s so far (a long draft takes a few minutes)']);
    expect([startProblem(400), startProblem(409), startProblem(502)].map((s) => s.length > 20)).toEqual([true, true, true]);
    expect(startProblem(409)).toContain('Another document');
    expect(plainName("Oravel’s  Stays; Limited", 'x')).toBe('Oravel s Stays Limited');
    expect(plainName('   ', 'Unnamed company')).toBe('Unnamed company');
    expect(plainName('a'.repeat(300), 'x')).toHaveLength(120);
    expect(termText({ name: 'total assets', printed: '305.00', page: 4 })).toBe('total assets: 305.00 (page 4)');
    expect(termText({ name: 'x', printed: '1', page: null })).toBe('x: 1');
  });
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe('the panel', () => {
  /** A server stand-in: answers the POST with `start` and each GET from `statuses` in turn; a scheduler that waits to be told to go on. */
  function rig(start: Response, statuses: unknown[]) {
    const calls: { method: string; body: unknown }[] = [];
    let n = 0;
    const fetcher = (async (_url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ method, body: init?.body ? JSON.parse(String(init.body)) : null });
      return method === 'POST' ? start.clone() : json(statuses[Math.min(n++, statuses.length - 1)]);
    }) as unknown as typeof fetch;
    const queue: (() => void)[] = [];
    const panel = new ForensicPanel(fetcher, (fn) => queue.push(fn));
    return { panel, calls, queue };
  }
  const pause = () => new Promise((r) => setTimeout(r));

  it('starts the job with the address and the plain names, follows it while it runs, and shows the report when it is done', async () => {
    const { panel, calls, queue } = rig(json({ state: 'running', url: URL, seconds: 0 }, 202), [{ state: 'running', url: URL, seconds: 3 }, { state: 'done', url: URL, report: report() }]);
    const started = panel.analyse(URL, 'Deon Energy Limited', 'DRHP');
    await pause();
    expect(calls[0]).toEqual({ method: 'POST', body: { url: URL, company: 'Deon Energy Limited', kind: 'DRHP' } });
    expect(panel.element.hidden).toBe(false);
    expect(panel.element.textContent).toContain('3 s so far');
    expect(queue).toHaveLength(1); // the next look at the job is waiting for its time
    queue.shift()!();
    await started;
    await pause();
    const text = panel.element.textContent ?? '';
    expect(text).toContain('Checks on Deon Energy Limited DRHP');
    expect(text).toContain('Concern level: low to moderate (1 established difference, 0 with one source only)');
    expect(text).toContain('Read: 520 pages, 1 sections found; not found: Risk factors; 2 pages without text.');
    expect(text).toContain('Checks: 93 held, 1 differ, 14 could not be run');
    expect(text).toContain('Shareholding tables');
    expect(text).toContain('Differences to look at (1)');
    expect(text).toContain('total assets: 305.00 (page 4)');
    expect(text).toContain('PDF page 4 (printed 2)');
    expect(text).toContain('Possible innocent explanations: a typing error that the final document corrects');
    expect(text).toContain('12 × not found on the page');
    expect(text).toContain('Not examined: text (no such agent is available yet).');
    expect(text).toContain('Not investment advice.');
    expect(/\b(buy|sell|avoid|subscribe)\b/i.test(text)).toBe(false);
  });

  it('says when nothing differs that this is not a clearance, and carries the job\'s own failure as it is', async () => {
    const clean = rig(json({}, 202), [{ state: 'done', url: URL, report: report({ flags: [], checks: { ...report().checks, differences: [] } }) }]);
    await clean.panel.analyse(URL, 'Deon', 'DRHP');
    expect(clean.panel.element.textContent).toContain('That is not a clearance');
    const failed = rig(json({}, 202), [{ state: 'failed', url: URL, error: 'the PDF has no text layer; it is a scan, which is not read' }]);
    await failed.panel.analyse(URL, 'Deon', 'DRHP');
    expect(failed.panel.element.textContent).toContain('which is not read');
    const other = rig(json({}, 202), [{ state: 'running', url: 'https://www.sebi.gov.in/other.pdf', seconds: 5 }]);
    await other.panel.analyse(URL, 'Deon', 'DRHP');
    expect(other.panel.element.textContent).toContain('Another document is being analysed');
  });

  it('tells the person when the server refuses or does not answer, and a newer request replaces an older one', async () => {
    for (const [status, words] of [[400, 'only a PDF on the SEBI site'], [409, 'Another document'], [500, 'could not be started']] as const) {
      const { panel } = rig(json({}, status), [{ state: 'idle' }]);
      await panel.analyse(URL, 'Deon', 'DRHP');
      expect(panel.element.textContent).toContain(words);
    }
    const garbled = rig(json({}, 202), [{ nonsense: true }]);
    await garbled.panel.analyse(URL, 'Deon', 'DRHP');
    expect(garbled.panel.element.textContent).toContain('did not answer');
    const { panel, queue } = rig(json({}, 202), [{ state: 'running', url: URL, seconds: 1 }, { state: 'done', url: URL, report: report() }]);
    const first = panel.analyse(URL, 'Deon', 'DRHP');
    await pause();
    panel.close();
    expect(panel.element.hidden).toBe(true);
    queue.shift()!(); // the old poll wakes up after the panel was closed
    await first;
    await pause();
    expect(panel.element.hidden).toBe(true);
    expect(panel.element.textContent).toBe('');
  });
});

describe('the Analyse button in the IPO tab', () => {
  const filing = { company: 'Carlsberg India Limited', kind: 'DRHP', filed: '2026-10-26', title: 'Carlsberg India Limited - DRHP', page: 'https://www.sebi.gov.in/filings/x.html', pdf: URL };
  const doc = { as_of: '2026-11-03', open: [], upcoming: [], closed: [], listed: [], filed: [filing, { ...filing, company: 'No Pdf Limited', pdf: '' }], sources: [], caveat: 'Display only.' };

  it('sits beside the document link of a draft that has one, and starts the checks for that document', async () => {
    const posts: unknown[] = [];
    const fetcher = (async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)));
        return json({}, 202);
      }
      return url === '/api/ipo' ? json(doc) : json({ state: 'running', url: URL, seconds: 1 });
    }) as unknown as typeof fetch;
    const view = new IpoView({ onSymbol: () => {} }, fetcher, new ForensicPanel(fetcher, () => {}));
    view.show();
    await new Promise((r) => setTimeout(r));
    const buttons = [...view.element.querySelectorAll('button')].filter((b) => b.textContent === 'Analyse') as HTMLButtonElement[];
    expect(buttons).toHaveLength(1); // the draft without a document has no button
    buttons[0].click();
    await new Promise((r) => setTimeout(r));
    expect(posts).toEqual([{ url: URL, company: 'Carlsberg India Limited', kind: 'DRHP' }]);
    expect(view.element.textContent).toContain('Checks on Carlsberg India Limited DRHP');
  });
});
