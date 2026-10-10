"""Does the free NSE data keep working from one day to the next? Each run asks every NSE source Athena depends on one
fixed question and appends the outcome to a log; `--report` says, per source and per day, how often it answered. Run it
from a scheduler for several days: one session cannot measure this."""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LOG = Path(".athena") / "nse_probe.jsonl"
IST = timezone(timedelta(hours=5, minutes=30))
ACTION_SYMBOLS = ("KOTAKBANK", "RELIANCE")  # both have a split or bonus on record, so an empty answer is a failure
SYMBOL_CHANGE_URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
MIN_SYMBOL_CHANGES = 500  # the file listed about 1,060 renames on Oct 10, 2026


@dataclass(frozen=True)
class Outcome:
    probe: str
    ok: bool
    detail: str


Probe = Callable[[], str]  # returns a short detail when the source answered correctly, raises when it did not


def corporate_actions(symbol: str) -> Probe:
    def run() -> str:
        from athena.price_integrity.nse_actions import NseActions

        rows = NseActions()(symbol)
        if not any(row.kind in ("split", "bonus") for row in rows):
            raise ValueError(f"no split or bonus row for {symbol} ({len(rows)} rows)")
        return f"{len(rows)} rows"

    return run


def announcements(symbol: str = "RELIANCE", days: int = 30) -> Probe:
    def run() -> str:
        from athena.news.nse_announcements import NseAnnouncements

        today = datetime.now(IST).date()
        items = NseAnnouncements()(symbol, today - timedelta(days=days), today)
        if not items:
            raise ValueError(f"no announcements for {symbol} in {days} days")
        return f"{len(items)} items"

    return run


def holiday_list() -> Probe:
    """One attempt, with none of the retry the app does, so the raw failure rate shows."""

    def run() -> str:
        from athena.loaders.nse_holidays import default_fetch, parse_holidays

        days = parse_holidays(default_fetch())
        return f"{len(days)} holidays"

    return run


def symbol_changes() -> Probe:
    def run() -> str:
        import requests

        from athena.price_integrity.nse_actions import HEADERS

        response = requests.get(SYMBOL_CHANGE_URL, headers=HEADERS, timeout=30)
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        rows = [line for line in response.text.splitlines() if line.count(",") >= 3]
        if len(rows) < MIN_SYMBOL_CHANGES:
            raise ValueError(f"only {len(rows)} rows")
        return f"{len(rows)} rows"

    return run


def default_probes() -> dict[str, Probe]:
    probes = {f"actions:{symbol}": corporate_actions(symbol) for symbol in ACTION_SYMBOLS}
    return {**probes, "announcements": announcements(), "holidays": holiday_list(), "symbol_changes": symbol_changes()}


def run_probes(probes: dict[str, Probe], log: Path = LOG, now: Callable[[], datetime] = lambda: datetime.now(IST)) -> list[Outcome]:
    """Run every probe once (a failure is recorded, never raised) and append one line per probe to `log`."""
    outcomes = []
    log.parent.mkdir(parents=True, exist_ok=True)
    cut_off = log.exists() and log.stat().st_size > 0 and not log.read_bytes().endswith(b"\n")  # a run killed mid-write
    with log.open("a", encoding="utf-8") as handle:
        if cut_off:
            handle.write("\n")  # so the next line does not join onto the broken one
        for name, probe in probes.items():
            started = time.monotonic()
            try:
                outcome = Outcome(name, True, probe())
            except Exception as exc:
                outcome = Outcome(name, False, f"{type(exc).__name__}: {exc}"[:200])
            handle.write(json.dumps({
                "at": now().isoformat(timespec="seconds"), "probe": outcome.probe, "ok": outcome.ok,
                "ms": round((time.monotonic() - started) * 1000), "detail": outcome.detail,
            }) + "\n")
            outcomes.append(outcome)
    return outcomes


def read_log(log: Path = LOG) -> list[dict]:
    if not log.exists():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a half-written line from a run that was cut off
    return rows


def report(rows: Sequence[dict]) -> str:
    """Per source: answers out of tries, the days it was seen on, its worst day and its longest run of failures."""
    if not rows:
        return "No probe runs logged yet."
    by_probe: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_probe[row["probe"]].append(row)
    days = sorted({row["at"][:10] for row in rows})
    lines = [f"{len(rows) // max(len(by_probe), 1)} runs over {len(days)} day(s): {days[0]} to {days[-1]}"]
    for name, group in sorted(by_probe.items()):
        ok = sum(row["ok"] for row in group)
        per_day: dict[str, list[bool]] = defaultdict(list)
        streak = longest = 0
        for row in group:
            per_day[row["at"][:10]].append(row["ok"])
            streak = 0 if row["ok"] else streak + 1
            longest = max(longest, streak)
        worst_day, worst = min(((d, sum(v) / len(v)) for d, v in per_day.items()), key=lambda pair: pair[1])
        failed = [row["detail"] for row in group if not row["ok"]]
        lines.append(
            f"  {name:22} {ok}/{len(group)} answered ({ok / len(group):.0%}); worst day {worst_day} {worst:.0%}; "
            f"longest failure run {longest}" + (f"; last error: {failed[-1]}" if failed else "")
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.evaluation.nse_probe", description="Probe the NSE sources and log the outcome.")
    parser.add_argument("--report", action="store_true", help="summarise the log instead of probing")
    parser.add_argument("--log", default=str(LOG))
    args = parser.parse_args(argv)
    log = Path(args.log)
    if args.report:
        print(report(read_log(log)))
        return 0
    outcomes = run_probes(default_probes(), log)
    for outcome in outcomes:
        print(f"{'ok  ' if outcome.ok else 'FAIL'} {outcome.probe:22} {outcome.detail}")
    return 0 if all(outcome.ok for outcome in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
