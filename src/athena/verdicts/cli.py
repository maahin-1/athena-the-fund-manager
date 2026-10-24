"""Verdicts from the market database.

    python -m athena.verdicts.cli --calibrate              # measure each analyst from the Phase 1k run logs; saves .athena/calibration.json
    python -m athena.verdicts.cli --symbol TCS             # make (or remake) one stock's verdict now and print it
    python -m athena.verdicts.cli --changed 25             # remake up to 25 verdicts that are out of date (new results, a large move)
    python -m athena.verdicts.cli --list                   # which stocks have a verdict, and which of those are out of date

Nothing here is scheduled; run it when you want it. Each run that calls the model writes `.athena/reports/verdicts_latest.md`."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from athena.clock import utc_now
from athena.llm.envfile import DEFAULT_ENV_FILE
from athena.verdicts.calibration import CALIBRATION_PATH, Calibration
from athena.verdicts.runner import VerdictRunner
from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

REPORT_DIR = Path(".athena") / "reports"
DEFAULT_LOGS = (Path(".athena") / "pit_runs" / "v2_unblinded.jsonl",)  # the live analysts see the real name, so these are the runs that match


def live_runner(
    db_path: Path | str = DEFAULT_PATH, env_file: Path | str = DEFAULT_ENV_FILE, calibration: Calibration | None = None, router: Any = None, council_mode: str | None = None
) -> VerdictRunner:
    """The runner wired to the configured model providers (the router reads the keys) and the saved calibration. A caller that already
    built a router passes it."""
    from athena.agents.base import Specialist
    from athena.agents.quant_technical import QUANT_TECHNICAL
    from athena.agents.news_sentiment import NEWS_SENTIMENT
    from athena.cli import FUNDAMENTAL_SPECIALISTS, GOOGLE_NEWS_SETTING
    from athena.llm.router import build_router

    router = router or build_router(env_file=env_file)
    client = router.client_for("specialist")
    specialists = {
        "quant_technical": Specialist(QUANT_TECHNICAL, client), **{n: Specialist(spec, client) for n, spec in FUNDAMENTAL_SPECIALISTS.items()},
        "news_sentiment": Specialist(NEWS_SENTIMENT, client),
    }
    model = ", ".join(getattr(client, "names", [])) or "unknown"
    from athena.llm.envfile import read_setting
    from athena.news.google_news import GoogleNews
    from athena.news.hub import NewsHub, default_sources
    from athena.news.nse_announcements import NseAnnouncements
    from athena.news.publisher_feeds import PublisherFeeds
    from athena.valuation.facts import live_facts

    google_on = read_setting(GOOGLE_NEWS_SETTING, env_file).strip() == "1"  # off unless the owner switches it on
    news = NewsHub(default_sources(NseAnnouncements(), PublisherFeeds(), GoogleNews(enabled=google_on)))

    council = None
    if council_mode:  # opt-in: the debate and the judge cost model calls (the judge on the strong tier)
        from athena.council.run import Council

        router = router or build_router(env_file=env_file)
        judge_client = router.client_for("judge")
        council = Council(client, judge_client, ", ".join(getattr(judge_client, "names", [])) or "unknown", council_mode)
    return VerdictRunner(db_path, specialists, calibration if calibration is not None else Calibration.load(), utc_now, model=model, model_facts=live_facts(), news=news, council=council)


def describe(stored: dict[str, Any]) -> str:
    payload = stored["payload"]
    verdict = payload["verdict"]
    lines = [
        f"{stored['symbol']}: {verdict['verdict']}, conviction {verdict['conviction']} ({verdict['calibration']}; raw {verdict['raw_conviction']})",
        f"  made {stored['made_at']:%Y-%m-%d %H:%M} UTC from prices to {stored['data_as_of']}; {payload['why']}",
    ]
    decision = payload.get("decision")
    if decision:
        lines.append(f"  decision: {decision['type']} ({decision['reason']})")
    council = payload.get("council")
    if council and council.get("judge"):
        j = council["judge"]
        lines.append("  council: " + (f"judge failed ({j['failed']})" if "failed" in j else f"{j['stance']} (blend {j['blend_rating']}), conviction {j['conviction']}; {j['why']}"))
    for name, out in payload["specialists"].items():
        lines.append(f"  {name}: {out['signal']} {out['confidence']} ({out['data_coverage']})")
    for name, why in payload["skipped"].items():
        lines.append(f"  {name}: not run, {why}")
    for risk in verdict["key_risks"][:3]:
        lines.append(f"  risk: {risk}")
    return "\n".join(lines)


def format_calibration(calibration: Calibration) -> str:
    lines = []
    for analyst, per in sorted(calibration.measures.items()):
        parts = ", ".join(f"{h}d: {m.accuracy:.0%} vs {m.best_fixed:.0%} fixed over {m.cases}" for h, m in sorted(per.items()))
        lines.append(f"{analyst}: trust {calibration.trust(analyst):.2f} ({parts})")
    return "\n".join(lines) or "nothing measured"


def write_report(summary: dict[str, Any], started: datetime, finished: datetime, report_dir: Path = REPORT_DIR) -> Path:
    lines = [
        f"# Verdict refresh, {started:%Y-%m-%d %H:%M} UTC",
        "",
        f"- Took {(finished - started).total_seconds() / 60:.1f} minutes; {summary['checked']} stocks with a verdict checked, {len(summary['changed'])} out of date, "
        f"{len(summary['made'])} made again, {summary['left']} left for the budget.",
    ]
    if summary["stopped"]:
        lines.append(f"- Stopped early: {summary['stopped']}.")
    lines += [f"- Made: {', '.join(summary['made'])}" if summary["made"] else "- Made: none"]
    lines += [f"- FAILED {line}" for line in summary["failed"]]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"verdicts_{started:%Y%m%d_%H%M}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (report_dir / "verdicts_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.verdicts.cli", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    parser.add_argument("--council", choices=("conflicts", "always"), help="also convene the council (debate, then the judge) beside the blend: on conflicts only, or for every verdict made")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--calibrate", nargs="*", metavar="LOG", help="run logs to measure (default: the Phase 1k v2 logs)")
    action.add_argument("--symbol")
    action.add_argument("--changed", type=int, metavar="N")
    action.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.calibrate is not None:
        paths = [Path(p) for p in args.calibrate] or [p for p in DEFAULT_LOGS if p.exists()]
        calibration = Calibration.from_logs(paths)
        calibration.save()
        print(format_calibration(calibration))
        print(f"saved to {CALIBRATION_PATH}")
        return 0
    if args.list:
        runner = VerdictRunner(args.db, {}, Calibration.load())
        with Warehouse(args.db, read_only=True, wait_seconds=60) as warehouse:
            symbols = warehouse.verdict_symbols()
        for symbol in symbols:
            state = runner.status(symbol)
            latest = state["verdict"]
            print(f"{symbol}: {latest['payload']['verdict']['verdict']} made {latest['made_at']:%Y-%m-%d}" + (f"; out of date: {'; '.join(state['reasons'])}" if state["reasons"] else ""))
        print(f"{len(symbols)} stocks with a verdict")
        return 0
    runner = live_runner(args.db, council_mode=args.council)
    started = utc_now()
    if args.symbol:
        print(describe(runner.make(args.symbol.upper())))
        return 0
    summary = runner.refresh_changed(args.changed, on_progress=print)
    print(write_report(summary, started, utc_now()))
    return 1 if summary["failed"] or summary["stopped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
