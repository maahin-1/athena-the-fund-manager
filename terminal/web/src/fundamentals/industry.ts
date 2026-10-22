import { fmtPlain, growthClass } from './format';
import type { IndustryBlock, IndustryRow } from './model';
import type { Cell, Table } from './tables';

const unitText = (row: IndustryRow, value: number): string => (row.unit === '%' ? `${fmtPlain(value, 1)}%` : `${fmtPlain(value, 1)}x`);

/** How the company stands against the industry: a ratio of the two for multiples (`0.92x`), a gap in percentage points for returns and margins (`+18.7 pts`).
 *  Only a gap is coloured: a higher return or margin than the industry is good, while a higher multiple is neither good nor bad on its own. */
export function versusCell(row: IndustryRow): Cell {
  if (row.relation === 'ratio') return { text: `${fmtPlain(row.compare, 2)}x`, cls: '' };
  const sign = row.compare > 0 ? '+' : '';
  return { text: `${sign}${fmtPlain(row.compare, 1)} pts`, cls: growthClass(row.compare) };
}

/** The rows of the "versus industry" table. */
export function industryTable(block: IndustryBlock): Table {
  return {
    head: ['This company', 'Industry', 'Versus'],
    rows: block.rows.map((row) => ({
      key: row.label,
      label: row.label,
      how: row.how,
      cells: [{ text: unitText(row, row.company), cls: '' }, { text: unitText(row, row.industry), cls: '' }, versusCell(row)],
    })),
  };
}

/** The line under the table: which industry, how it was matched, how many firms and which dated table, then any caution. */
export function industryNotes(block: IndustryBlock): string[] {
  const matched = block.method === 'nse_ticker' ? 'matched by NSE symbol' : block.method === 'name' ? 'matched by company name' : `matched by ${block.method ?? 'a rule'} at confidence ${fmtPlain((block.confidence ?? 0) * 100, 0)}%`;
  const lines = [`${block.name} (${block.n_firms ?? '?'} firms in the table dated ${block.data_as_of ?? 'unknown'}; ${matched}). Source: ${block.source}.`];
  for (const flag of block.flags) lines.push(`Caution: ${flag}.`);
  return lines;
}
