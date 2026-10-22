"""Rows of a workbook sheet, from either an old `.xls` (xlrd) or a `.xlsx` (openpyxl), as plain Python values (text, number, date, None)."""
from __future__ import annotations

import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any

from athena.contracts import AthenaError


def read_rows(path: str | Path, sheet: str) -> list[list[Any]]:
    """Every row of the named sheet; an empty cell is None, a date cell a `date`. An unknown sheet is an error that names the sheets there are."""
    p = Path(path)
    if not p.exists():
        raise AthenaError(f"no workbook at {p}")
    suffix = p.suffix.lower()
    if suffix == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(p))
        if sheet not in book.sheet_names():
            raise AthenaError(f"{p.name} has no sheet {sheet!r}; it has {', '.join(book.sheet_names())}")
        sh = book.sheet_by_name(sheet)
        rows: list[list[Any]] = []
        for r in range(sh.nrows):
            row: list[Any] = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                    row.append(None)
                elif cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate_as_datetime(cell.value, book.datemode).date())
                elif cell.ctype == xlrd.XL_CELL_TEXT:
                    row.append(cell.value if cell.value != "" else None)
                else:
                    row.append(cell.value)
            rows.append(row)
        return rows
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            book = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        try:
            if sheet not in book.sheetnames:
                raise AthenaError(f"{p.name} has no sheet {sheet!r}; it has {', '.join(book.sheetnames)}")
            return [[v.date() if isinstance(v, datetime) else v for v in values] for values in book[sheet].iter_rows(values_only=True)]
        finally:
            book.close()
    raise AthenaError(f"{p.name}: only .xls and .xlsx workbooks are read")


def serial_to_date(value: Any) -> date | None:
    """A date from an Excel serial number (the 1900 system), a datetime or a date; None for anything else."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:
        import xlrd

        return xlrd.xldate_as_datetime(value, 0).date()
    return None
