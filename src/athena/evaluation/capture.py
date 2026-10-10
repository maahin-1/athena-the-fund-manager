from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from athena.clock import utc_now
from athena.cli import LiveSources, live_sources
from athena.contracts import AthenaError
from athena.evaluation.golden import GOLDEN_DIR, Snapshot, snapshot_to_json
from athena.loaders.fundamentals import DATASET
from athena.loaders.index_valuation import valuation_history
from athena.resolver import Ambiguity


def capture_symbol(sources: LiveSources, symbol: str) -> Snapshot:
    """One dated copy of everything the specialists would read for `symbol`: its price history and, for a stock, its
    statements. Raises an `AthenaError` when the symbol is unknown or has no prices."""
    resolved = sources.resolver.resolve(symbol)
    if isinstance(resolved, Ambiguity):
        raise AthenaError(f"{symbol!r} is ambiguous; give the exact symbol")
    bars = tuple(sources.bars(resolved.identifier))
    if not bars:
        raise AthenaError(f"no prices for {resolved.identifier}")
    payload, as_of = None, None
    if resolved.asset_class == "equity":
        sources.fundamentals._loader.refresh(symbol=resolved.identifier)
        record = sources.fundamentals._loader.read(DATASET, resolved.identifier)
        payload, as_of = record.payload, record.as_of
    return Snapshot(resolved.identifier, resolved.name, resolved.asset_class, utc_now(), bars, payload, as_of)


def write_snapshot(snapshot: Snapshot, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot.symbol}.json"
    path.write_text(json.dumps(snapshot_to_json(snapshot), separators=(",", ":")), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze today's data for golden-set instruments into tests/golden.")
    parser.add_argument("symbols", nargs="+", help="exact NSE symbols")
    parser.add_argument("--dir", type=Path, default=GOLDEN_DIR, help="the golden directory (default: tests/golden)")
    args = parser.parse_args(argv)
    sources = live_sources()
    failed = 0
    for symbol in args.symbols:
        try:
            path = write_snapshot(capture_symbol(sources, symbol), args.dir / "snapshots")
            print(f"{symbol}: {path}")
        except AthenaError as exc:
            failed += 1
            print(f"{symbol}: skipped, {exc}")
    index_path = args.dir / "index_valuation.json"
    history = {day.isoformat(): row for day, row in sorted(valuation_history(sources.store).items())}
    index_path.write_text(json.dumps(history, separators=(",", ":")), encoding="utf-8")
    print(f"index valuation: {len(history)} days -> {index_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
