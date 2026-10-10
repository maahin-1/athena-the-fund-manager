from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from athena.clock import utc_now
from athena.cli import LiveSources, live_sources
from athena.contracts import AthenaError
from dataclasses import replace

from athena.evaluation.golden import GOLDEN_DIR, Snapshot, load_snapshot, snapshot_to_json
from athena.loaders.fundamentals import DATASET
from athena.loaders.index_valuation import valuation_history
from athena.orchestrator.builders import history_fetcher
from athena.resolver import Ambiguity


def capture_symbol(sources: LiveSources, symbol: str) -> Snapshot:
    """One dated copy of everything the specialists would read for `symbol`: its price history and, for a stock, its
    statements. Raises an `AthenaError` when the symbol is unknown or has no prices."""
    resolved = sources.resolver.resolve(symbol)
    if isinstance(resolved, Ambiguity):
        raise AthenaError(f"{symbol!r} is ambiguous; give the exact symbol")
    bars = tuple(history_fetcher(sources.chain)(resolved.identifier))  # raw: the snapshot keeps what the exchange printed
    if not bars:
        raise AthenaError(f"no prices for {resolved.identifier}")
    payload, as_of = None, None
    if resolved.asset_class == "equity":
        sources.fundamentals._loader.refresh(symbol=resolved.identifier)
        record = sources.fundamentals._loader.read(DATASET, resolved.identifier)
        payload, as_of = record.payload, record.as_of
    return Snapshot(
        resolved.identifier, resolved.name, resolved.asset_class, utc_now(), bars, payload, as_of, _events(sources, resolved.identifier),
        _actions(sources, resolved.identifier),
    )


def _events(sources: LiveSources, symbol: str) -> tuple:
    try:
        return tuple(sources.events(symbol))
    except AthenaError as exc:
        print(f"{symbol}: no corporate events recorded, {exc}")
        return ()


def _actions(sources: LiveSources, symbol: str) -> tuple:
    try:
        return tuple(sources.actions(symbol))
    except AthenaError as exc:
        print(f"{symbol}: no NSE corporate actions recorded, {exc}")
        return ()


def backfill_events(sources: LiveSources, directory: Path) -> list[str]:
    """Add today's recorded events to snapshots captured before they carried any, leaving their bars untouched."""
    done = []
    for path in sorted((directory / "snapshots").glob("*.json")):
        snapshot = load_snapshot(path.stem, directory / "snapshots")
        write_snapshot(
            replace(snapshot, events=_events(sources, snapshot.symbol), actions=_actions(sources, snapshot.symbol)), directory / "snapshots"
        )
        done.append(snapshot.symbol)
    return done


def write_snapshot(snapshot: Snapshot, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot.symbol}.json"
    path.write_text(json.dumps(snapshot_to_json(snapshot), separators=(",", ":")), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze today's data for golden-set instruments into tests/golden.")
    parser.add_argument("symbols", nargs="*", help="exact NSE symbols")
    parser.add_argument("--backfill-events", action="store_true", help="add recorded events to existing snapshots, keeping their bars")
    parser.add_argument("--dir", type=Path, default=GOLDEN_DIR, help="the golden directory (default: tests/golden)")
    args = parser.parse_args(argv)
    sources = live_sources()
    if args.backfill_events:
        print("events added to:", ", ".join(backfill_events(sources, args.dir)))
        return 0
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
