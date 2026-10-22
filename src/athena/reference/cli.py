"""python -m athena.reference load | audit | input

    load    read the Damodaran mirror (industry workbooks, country files, company list) into the reference store, build the company map, seed the dated inputs
    audit   how far the data reaches: dataset dates, industries, small samples, company-map coverage by pass
    rates   fetch the US 10-year Treasury yield and store it as a dated input
    input   set a dated input (risk-free rate, tax rate, ...) by hand; the newest value on or before a date is the one used"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb

from athena.contracts import AthenaError
from athena.reference import company_map as cm
from athena.reference import country, damodaran, rates
from athena.reference.store import DEFAULT_PATH, ReferenceStore

MIRROR = Path(".athena") / "damodaran"
WAREHOUSE = Path(".athena") / "warehouse.duckdb"
FUNDAMENTALS = Path(".athena") / "fundamentals.duckdb"
SMALL_SAMPLE = 10
ASSUMED = "assumption seeded 2026-10-22, to be reviewed"
SEEDS = (
    ("tax_marginal", date(2026, 4, 1), 0.2517, "owner decision 2026-10-22", "concessional-regime statutory rate (22% + surcharge + cess); to be confirmed against an official source. Damodaran's file uses 0.30"),
    ("inflation_inr", date(2026, 4, 1), 0.04, ASSUMED, "expected long-run rupee inflation: the RBI's target; turns dollar rates into rupee rates"),
    ("inflation_usd", date(2026, 4, 1), 0.02, ASSUMED, "expected long-run dollar inflation: the Federal Reserve's target"),
    ("large_firm_cap_crore", date(2026, 4, 1), 42000.0, ASSUMED, "market value from which a company is rated on the large-firm interest-cover table: about US$5 billion at Rs 84 to the dollar"),
    ("debt_maturity_years", date(2026, 4, 1), 3.0, ASSUMED, "average maturity used to value debt as one coupon bond (his sample uses 3)"),
    ("band_erp", date(2026, 4, 1), 0.005, ASSUMED, "equity risk premium moved by this much in the low and high cases"),
    ("band_rf", date(2026, 4, 1), 0.005, ASSUMED, "risk-free rate moved by this much in the low and high cases"),
    ("band_beta", date(2026, 4, 1), 0.15, ASSUMED, "unlevered beta moved by this much in the low and high cases"),
)


def universe(warehouse: Path, fundamentals: Path) -> list[cm.Company]:
    """Listed stocks (not ETFs) with their names and any NSE industry label the exchange files gave."""
    if not warehouse.exists():
        raise AthenaError(f"no warehouse at {warehouse}")
    w = duckdb.connect(str(warehouse), read_only=True)
    try:
        stocks = w.execute("SELECT symbol, coalesce(name, symbol) FROM screen WHERE kind = 'stock' ORDER BY symbol").fetchall()
        members = dict(w.execute("SELECT symbol, any_value(industry) FROM index_members WHERE industry IS NOT NULL AND industry <> '' GROUP BY symbol").fetchall())
    finally:
        w.close()
    company_labels: dict[str, str] = {}
    if fundamentals.exists():
        f = duckdb.connect(str(fundamentals), read_only=True)
        try:
            company_labels = dict(f.execute("SELECT symbol, industry FROM companies WHERE industry IS NOT NULL AND industry <> ''").fetchall())
        finally:
            f.close()
    labels = cm.labels_from(members, company_labels)
    return [cm.Company(symbol, name, labels.get(symbol, "")) for symbol, name in stocks]


def cmd_load(args: argparse.Namespace) -> int:
    datasets = Path(args.mirror) / "site" / "pc" / "datasets"
    with ReferenceStore(args.store) as store:
        report = damodaran.load_industry_dir(store, datasets, log=print)
        print(f"industry workbooks: {report.files} loaded, {len(report.skipped)} skipped, {report.rows} rows")
        loaded = country.load_country_files(store, datasets)
        print("country files: " + ", ".join(str(f.as_of) for f in loaded))
        as_of = store.latest_stat_date("damodaran", "India") or date.today()
        india = cm.read_industry_rows(datasets / "indname.xlsx")
        people = universe(Path(args.warehouse), Path(args.fundamentals))
        mapped = cm.build_company_map(india, people)
        store.put_company_industries(cm.as_rows(mapped, as_of))
        print(f"company map: {len(mapped)} of {len(people)} stocks ({cm.audit(people, mapped)['by_method']})")
        for key, day, value, source, note in SEEDS:
            if store.input(key, date.today()) is None:
                store.put_input(key, day, value, source, note)
                print(f"seeded input {key} = {value}")
        _rates(store)
    return 1 if report.skipped else 0


def _rates(store: ReferenceStore) -> int:
    try:
        day, value = rates.store_us_10y(store)
    except (AthenaError, OSError, ValueError) as exc:  # a failed fetch leaves the last stored yield in place
        print(f"US 10-year yield not updated: {exc}")
        return 1
    print(f"US 10-year yield {value:.4f} on {day}")
    return 0


def cmd_rates(args: argparse.Namespace) -> int:
    with ReferenceStore(args.store) as store:
        return _rates(store)


def cmd_audit(args: argparse.Namespace) -> int:
    with ReferenceStore(args.store, read_only=True) as store:
        as_of = store.latest_stat_date("damodaran", "India")
        print(f"India industry tables dated {as_of}; {len(store.industries())} industries")
        small = []
        for industry in store.industries():
            n = next((n for _, n in store.industry_stats(industry).values() if n is not None), None)
            if n is not None and n < SMALL_SAMPLE:
                small.append((industry, n))
        print(f"industries with fewer than {SMALL_SAMPLE} firms: {len(small)}")
        for industry, n in sorted(small, key=lambda t: t[1]):
            print(f"  {industry}: {n}")
    people = universe(Path(args.warehouse), Path(args.fundamentals))
    with ReferenceStore(args.store, read_only=True) as store:
        stored = store.company_industries()
    mapped = [cm.MapRow(c.symbol, *stored[c.symbol]) for c in people if c.symbol in stored]
    report = cm.audit(people, mapped)
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0


def cmd_input(args: argparse.Namespace) -> int:
    with ReferenceStore(args.store) as store:
        store.put_input(args.key, date.fromisoformat(args.as_of), args.value, args.source, args.note)
    print(f"{args.key} = {args.value} as of {args.as_of}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.reference", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default=str(DEFAULT_PATH))
    parser.add_argument("--warehouse", default=str(WAREHOUSE))
    parser.add_argument("--fundamentals", default=str(FUNDAMENTALS))
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load")
    load.add_argument("--mirror", default=str(MIRROR))
    load.set_defaults(run=cmd_load)
    sub.add_parser("audit").set_defaults(run=cmd_audit)
    sub.add_parser("rates").set_defaults(run=cmd_rates)
    put = sub.add_parser("input")
    put.add_argument("key")
    put.add_argument("value", type=float)
    put.add_argument("--as-of", default=date.today().isoformat())
    put.add_argument("--source", default="owner")
    put.add_argument("--note", default="")
    put.set_defaults(run=cmd_input)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except AthenaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
