import { COLUMNS, type Row } from '../screenlogic';

/** The rows of a screen as CSV, for a spreadsheet: the labels as the table shows them (a percentage column marked and written in percent), missing figures
 *  empty, text quoted when it must be. Text that starts like a formula gets a leading apostrophe, so opening the file cannot run one. */
export function toCsv(rows: Row[], columns: string[]): string {
  const head = columns.map((c) => cell(`${COLUMNS[c]?.[0] ?? c}${COLUMNS[c]?.[1] === 'pct' ? ' (%)' : ''}`));
  const body = rows.map((row) =>
    columns
      .map((c) => {
        const v = row[c];
        if (v === null || v === undefined) return '';
        if (typeof v === 'number') return String(COLUMNS[c]?.[1] === 'pct' ? round(v * 100) : round(v));
        return cell(v);
      })
      .join(','),
  );
  return [head.join(','), ...body].map((line) => `${line}\r\n`).join('');
}

const round = (n: number): number => Math.round(n * 1e4) / 1e4;

function cell(text: string): string {
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}
