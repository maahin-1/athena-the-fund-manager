"""The share count of each company, chosen from what its filings say.

A filing gives paid-up equity capital and face value, so it states a share count; the filing's net profit over its earnings per share gives a second, rough one. Counts go
wrong in two ways: a filer reports a figure at the wrong scale (one filing 100,000 times too large), and a count from before a bonus or split is read after the newer one.
So every filing's counts are kept (`share_counts`) and the company's count is the newest period's declared count, set aside when it disagrees with its own profit
and earnings per share by more than `UNIT_ERROR_FACTOR` (a scale error is a factor of 100 or more; a bonus or a minority share of profit is a factor of a few). When no
declared count survives, the newest profit-and-EPS count is used and said to be approximate.

    python -m athena.fundamentals.shares audit                 how stored counts compare with profit / EPS
    python -m athena.fundamentals.shares backfill [--apply]    read every cached filing, record its counts, choose each company's (a dry run unless --apply)"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, open_read
from athena.pit.nse_results import CACHE_DIR

UNIT_ERROR_FACTOR = 5.0
CROSS_FILING_FACTOR = 12.0  # a count with no EPS to check it is set aside when it is this far from the company's other filings (the largest usual split is 10 for 1)
RECENT_FILINGS = 8  # the backfill reads this many of the newest filings per company


@dataclass(frozen=True)
class Choice:
    shares: float | None
    how: str  # "declared", "from profit and EPS (approximate)" or "none"
    period_end: object | None = None


def agrees(declared: float, from_profit: float | None) -> bool:
    """Whether a declared count is in step with the filing's own profit and EPS. A filing with no EPS cannot disagree."""
    if not from_profit or from_profit <= 0:
        return True
    return 1.0 / UNIT_ERROR_FACTOR <= declared / from_profit <= UNIT_ERROR_FACTOR


def _reference(counts: Sequence[dict[str, Any]]) -> float | None:
    values = sorted(c["from_profit"] for c in counts if c.get("from_profit") and c["from_profit"] > 0)  # not the declared counts: filers' scale errors can be most of them
    if not values:
        return None
    m = len(values) // 2
    return values[m] if len(values) % 2 else (values[m - 1] + values[m]) / 2


def choose(counts: Sequence[dict[str, Any]]) -> Choice:
    """The company's share count from its filings' counts, given newest period first."""
    reference = _reference(counts)
    for c in counts:
        declared, own = c.get("declared"), c.get("from_profit")
        if not declared or declared <= 0:
            continue
        if own and own > 0:
            ok = agrees(declared, own)  # the filing's own profit and EPS decide
        else:
            ok = reference is None or 1.0 / CROSS_FILING_FACTOR <= declared / reference <= CROSS_FILING_FACTOR
        if ok:
            return Choice(float(declared), "declared", c.get("period_end"))
    for c in counts:
        if c.get("from_profit") and c["from_profit"] > 0:
            return Choice(float(c["from_profit"]), "from profit and EPS (approximate)", c.get("period_end"))
    return Choice(None, "none")


def refresh(store: FundamentalsStore, symbol: str) -> Choice:
    """Set the company's stored count from its filings' counts. A company with no counts keeps what it has."""
    choice = choose(store.share_counts(symbol))
    if choice.shares is not None:
        store.set_shares(symbol, choice.shares)
    return choice


def cache_path(url: str, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".xml")


def backfill(path: Path, cache_dir: Path, apply: bool, recent: int = RECENT_FILINGS, out=sys.stdout, report: Path | None = None) -> dict[str, int]:
    """Read the newest cached filings of every company, record their counts and choose each company's. Returns counts of what happened."""
    stats: Counter[str] = Counter()
    changes: list[tuple[str, float | None, float | None, str]] = []
    with open_read(path) if not apply else FundamentalsStore(path) as store:
        symbols = [r[0] for r in store._con.execute("SELECT symbol FROM companies ORDER BY symbol").fetchall()]
        for symbol in symbols:
            filings = store._con.execute(
                "SELECT url, basis, period_end, filed FROM filings WHERE symbol = ? AND status = 'ok' ORDER BY period_end DESC, filed DESC LIMIT ?", [symbol, recent]
            ).fetchall()
            counts: list[dict[str, Any]] = []
            for url, basis, period_end, filed in filings:
                file = cache_path(url, cache_dir)
                if not file.exists():
                    stats["filing not cached"] += 1
                    continue
                try:
                    st = parse_rows(file.read_text(encoding="utf-8"))
                except ValueError:
                    stats["filing unreadable"] += 1
                    continue
                counts.append({"url": url, "basis": basis, "period_end": period_end, "filed": filed, "declared": st.shares_declared, "from_profit": st.shares_from_profit})
                if apply:
                    store.save_share_count(url, symbol, basis, period_end, filed, st.shares_declared, st.shares_from_profit)
            choice = choose(counts)
            old = store.company(symbol)["shares"] if store.company(symbol) else None
            if choice.shares is None:
                stats["no count found"] += 1
                continue
            stats[f"chosen: {choice.how}"] += 1
            if old is None or abs(choice.shares - old) > 0.005 * max(old, 1.0):
                stats["changed"] += 1
                changes.append((symbol, old, choice.shares, choice.how))
                if apply:
                    store.set_shares(symbol, choice.shares)
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        lines = ["symbol,old,new,how"] + [f"{s},{'' if o is None else f'{o:.0f}'},{n:.0f},{h}" for s, o, n, h in changes]
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for symbol, old, new, how in changes[:25]:
        before = "none" if old is None else f"{old:,.0f}"
        print(f"  {symbol:12} {before:>20} -> {new:>16,.0f}  ({how})", file=out)
    return dict(stats)


def audit(path: Path, out=sys.stdout) -> dict[str, int]:
    """How far each company's stored count is from its newest filing's profit / EPS count."""
    buckets: Counter[str] = Counter()
    with open_read(path) as store:
        rows = store._con.execute("SELECT symbol, shares FROM companies").fetchall()
        for symbol, shares in rows:
            counts = store.share_counts(symbol)
            ref = next((c["from_profit"] for c in counts if c["from_profit"]), None)
            if not shares or not ref:
                buckets["no check available"] += 1
                continue
            ratio = shares / ref
            buckets["within 2x" if 0.5 <= ratio <= 2 else "within 5x" if 0.2 <= ratio <= 5 else "more than 5x apart"] += 1
    for key, n in buckets.most_common():
        print(f"{n:6d}  {key}", file=out)
    return dict(buckets)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.fundamentals.shares", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["audit", "backfill"])
    parser.add_argument("--apply", action="store_true", help="write the counts and the choice (backfill is a dry run without it)")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    parser.add_argument("--cache", default=str(CACHE_DIR))
    parser.add_argument("--report", default=str(Path(".athena") / "reports" / "share-count-changes.csv"), help="where backfill lists every change (CSV)")
    args = parser.parse_args(argv)
    if args.command == "audit":
        audit(Path(args.path))
        return 0
    stats = backfill(Path(args.path), Path(args.cache), args.apply, report=Path(args.report))
    for key, n in sorted(stats.items()):
        print(f"{n:6d}  {key}")
    print("applied" if args.apply else "dry run: nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
