"""Damodaran's published industry workbooks (the `*India.xls` family and its regional siblings) read into reference rows.

Every workbook has an "Industry Averages" sheet: a few lines of notes with a "Date updated:" cell, a header row that starts "Industry Name", then one row per industry and a
"Total Market" row. A column becomes a metric named by its header (lower case, punctuation as underscores); "Number of firms" becomes the firm count of the row. A workbook
that does not look like that is reported and left out, never half read."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from athena.contracts import AthenaError
from athena.reference.sheets import read_rows, serial_to_date
from athena.reference.store import ReferenceStore, StatRow

SHEET = "Industry Averages"
SOURCE = "damodaran"
FIRM_COUNT = {"number_of_firms", "number_of_firm", "no_of_firms", "number_firms"}
TOTAL_ROWS = ("Total Market", "Total Market (without financials)")


@dataclass
class LoadReport:
    files: int = 0
    rows: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def slug(header: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", header.strip().lower())).strip("_")[:70]


def dataset_name(path: str | Path, suffix: str) -> str:
    """`waccIndia.xls` with suffix `India` is the dataset `wacc`; a file without the suffix keeps its whole name."""
    stem = Path(path).stem
    return stem[: -len(suffix)] if suffix and stem.endswith(suffix) and len(stem) > len(suffix) else stem


def parse_industry_workbook(path: str | Path, region: str, suffix: str) -> list[StatRow]:
    """The statistics of one workbook. Raises AthenaError when the sheet, the date or the header row is missing."""
    p = Path(path)
    rows = read_rows(p, SHEET)
    as_of = next((serial_to_date(r[1]) for r in rows[:15] if r and isinstance(r[0], str) and r[0].strip().lower().startswith("date updated") and len(r) > 1), None)
    if as_of is None:
        raise AthenaError(f"{p.name}: no 'Date updated:' cell with a date")
    header_at = next((i for i, r in enumerate(rows[:40]) if r and isinstance(r[0], str) and r[0].strip().lower().startswith("industry")), None)
    if header_at is None:
        raise AthenaError(f"{p.name}: no header row starting 'Industry'")
    metrics: dict[int, str] = {}
    firms_at: int | None = None
    seen: dict[str, int] = {}
    for col, text in enumerate(rows[header_at]):
        if col == 0 or not isinstance(text, str) or not text.strip():
            continue
        name = slug(text)
        if not name:
            continue
        if name in FIRM_COUNT:
            firms_at = col
            continue
        seen[name] = seen.get(name, 0) + 1
        metrics[col] = name if seen[name] == 1 else f"{name}_{seen[name]}"
    dataset = dataset_name(p, suffix)
    out: list[StatRow] = []
    for row in rows[header_at + 1 :]:
        industry = row[0].strip() if row and isinstance(row[0], str) else ""
        if not industry:
            continue
        n_firms = None
        if firms_at is not None and firms_at < len(row) and isinstance(row[firms_at], (int, float)) and not isinstance(row[firms_at], bool):
            n_firms = int(row[firms_at])
        values = [(name, row[col]) for col, name in metrics.items() if col < len(row) and isinstance(row[col], (int, float)) and not isinstance(row[col], bool)]
        if not values:
            continue  # a note under the table, or an industry with nothing reported
        out.extend(StatRow(SOURCE, region, dataset, industry, name, float(v), n_firms, as_of, p.name) for name, v in values)
    if not out:
        raise AthenaError(f"{p.name}: the {SHEET!r} sheet has no industry rows")
    return out


def load_industry_dir(
    store: ReferenceStore, directory: str | Path, region: str = "India", suffix: str = "India", log: Callable[[str], None] = lambda _: None
) -> LoadReport:
    """Load every `*<suffix>.xls` workbook in the directory. A workbook that cannot be read is listed in the report, and the rest still load."""
    report = LoadReport()
    for path in sorted(Path(directory).glob(f"*{suffix}.xls")):
        try:
            rows = parse_industry_workbook(path, region, suffix)
        except AthenaError as exc:
            report.skipped.append((path.name, str(exc)))
            log(f"skipped {path.name}: {exc}")
            continue
        report.rows += store.put_stats(rows)
        report.files += 1
    return report


def total_market(store: ReferenceStore, on_or_before: date | None = None, region: str = "India") -> dict[str, tuple[float, int | None]]:
    return store.industry_stats("Total Market", region=region, on_or_before=on_or_before)
