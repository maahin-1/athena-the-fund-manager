"""Damodaran's country risk workbooks (`ctryprem*.xlsx`): the mature-market equity risk premium, the multiplier on default spreads and, for every country, the sovereign rating,
default spread, total equity risk premium and country risk premium. Several dated versions exist (the workbook is revised a few times a year), so each is loaded under its own date
and a valuation for a past date reads the newest one published on or before it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from athena.contracts import AthenaError
from athena.reference.sheets import read_rows, serial_to_date
from athena.reference.store import ReferenceStore

SHEET = "ERPs by country"
SOURCE = "damodaran ctryprem"


@dataclass(frozen=True)
class CountryFile:
    as_of: date
    mature_erp: float
    us_erp: float | None
    vol_multiplier: float | None
    rows: list[tuple[str, str, str, float, float, float]]  # country, region, rating, default spread, total ERP, country risk premium


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _first_number(row: list) -> float | None:
    """The first numeric cell after the label (the value sits in different columns in different versions of the workbook)."""
    return next((float(v) for v in row[1:] if isinstance(v, (int, float)) and not isinstance(v, bool)), None)


def parse_country_workbook(path: str | Path) -> CountryFile:
    p = Path(path)
    rows = read_rows(p, SHEET)
    as_of = mature = us = multiplier = None
    header_at = None
    for i, r in enumerate(rows[:30]):
        label = r[0].strip().lower() if r and isinstance(r[0], str) else ""
        if label.startswith("date of update") and len(r) > 1:
            as_of = next((d for d in (serial_to_date(v) for v in r[1:4]) if d), None)
        elif label.startswith("enter the current risk premium for a mature"):
            mature = _first_number(r)
        elif label.startswith("enter the current risk premium for the us"):
            us = _first_number(r)
        elif label.startswith("if yes, enter the multiplier"):
            multiplier = _first_number(r)
        elif label == "country" and header_at is None:
            header_at = i
    if as_of is None or mature is None or header_at is None:
        raise AthenaError(f"{p.name}: the {SHEET!r} sheet lacks its date, the mature market premium or the country table")
    head = [str(h).strip().lower() if h is not None else "" for h in rows[header_at]]

    def column(*words: str) -> int:
        for i, h in enumerate(head):
            if all(w in h for w in words):
                return i
        raise AthenaError(f"{p.name}: no column with {' '.join(words)!r} in the country table")

    rating, spread, total, crp = column("rating"), column("default spread"), column("total equity risk premium"), column("country risk premium")
    out = []
    for r in rows[header_at + 1 :]:
        if not r or not isinstance(r[0], str) or not r[0].strip():
            if out:
                break
            continue
        values = [_number(r[c]) if c < len(r) else None for c in (spread, total, crp)]
        if None in values or not isinstance(r[rating], str):
            continue
        out.append((r[0].strip(), str(r[1] or ""), r[rating].strip(), values[0], values[1], values[2]))
    if not out:
        raise AthenaError(f"{p.name}: the country table has no rows")
    return CountryFile(as_of, mature, us, multiplier, out)


def load_country_files(store: ReferenceStore, directory: str | Path) -> list[CountryFile]:
    """Load every dated `ctryprem*.xlsx`; each also writes its mature-market premium (`erp_mature`), US premium (`erp_us`) and multiplier (`country_vol_multiplier`) as dated inputs."""
    loaded = []
    for path in sorted(Path(directory).glob("ctryprem*.xlsx")):
        if not re.search(r"ctryprem([A-Za-z]+\d{2})?\.xlsx$", path.name):
            continue
        parsed = parse_country_workbook(path)
        store.put_country_premiums([(c, region, rating, spread, total, crp, parsed.as_of) for c, region, rating, spread, total, crp in parsed.rows])
        store.put_input("erp_mature", parsed.as_of, parsed.mature_erp, SOURCE, path.name)
        if parsed.us_erp is not None:
            store.put_input("erp_us", parsed.as_of, parsed.us_erp, SOURCE, path.name)
        if parsed.vol_multiplier is not None:
            store.put_input("country_vol_multiplier", parsed.as_of, parsed.vol_multiplier, SOURCE, path.name)
        loaded.append(parsed)
    return loaded
