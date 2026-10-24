"""A gauge of the market's state, from the benchmark index alone, by written rules.

    python -m athena.market.regime [--index "Nifty 50"]

The state is one of CALM, NORMAL, STRESSED, with the figures behind it: the index's realised volatility over the last 21 trading days and where that sits among the same measure on every
earlier day held (a percentile), the fall from its highest close in the last 252 days, the distance of the close from its 200-day average, and the P/E percentile when the index file has it.
Rules: STRESSED when volatility is in the top tenth of its history or the index is 15% or more below its 252-day high; CALM when volatility is in the bottom third and the fall is under 5%;
otherwise NORMAL. Valuation (a high P/E percentile) is reported beside the state, not folded into it: a dear market can be calm. The gauge describes; it does not forecast."""
from __future__ import annotations

import argparse
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

VOL_DAYS = 21
HIGH_DAYS = 252
TREND_DAYS = 200
STRESS_VOL_PERCENTILE = 0.90
STRESS_DRAWDOWN = 0.15
CALM_VOL_PERCENTILE = 1 / 3
CALM_DRAWDOWN = 0.05
MIN_HISTORY = 252 + VOL_DAYS
CALM, NORMAL, STRESSED = "CALM", "NORMAL", "STRESSED"


def _vol(closes: Sequence[float]) -> float:
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    return statistics.pstdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0


def gauge(closes: Mapping[date, float], pe: Mapping[date, float] | None = None) -> dict[str, Any] | None:
    """The state on the latest day of `closes`, or None when fewer than `MIN_HISTORY` days are held."""
    days = sorted(closes)
    if len(days) < MIN_HISTORY:
        return None
    series = [closes[d] for d in days]
    vols = [_vol(series[i - VOL_DAYS : i + 1]) for i in range(VOL_DAYS, len(series))]
    now_vol = vols[-1]
    percentile = sum(1 for v in vols if v <= now_vol) / len(vols)
    high = max(series[-HIGH_DAYS:])
    drawdown = 1.0 - series[-1] / high
    average = statistics.fmean(series[-TREND_DAYS:]) if len(series) >= TREND_DAYS else None
    state = STRESSED if percentile >= STRESS_VOL_PERCENTILE or drawdown >= STRESS_DRAWDOWN else CALM if percentile <= CALM_VOL_PERCENTILE and drawdown < CALM_DRAWDOWN else NORMAL
    out: dict[str, Any] = {
        "day": days[-1].isoformat(), "state": state, "realised_vol": now_vol, "vol_percentile": percentile, "vol_history_days": len(vols), "drawdown_from_252d_high": drawdown,
        "close_vs_200d_average": series[-1] / average - 1.0 if average else None, "pe": None, "pe_percentile": None,
    }
    if pe:
        history = [pe[d] for d in sorted(pe)]
        if len(history) >= 250:
            out["pe"] = history[-1]
            out["pe_percentile"] = sum(1 for v in history if v <= history[-1]) / len(history)
    return out


def format_gauge(g: Mapping[str, Any] | None) -> str:
    if g is None:
        return f"not enough index history (needs {MIN_HISTORY} trading days)"
    trend = "n/a" if g["close_vs_200d_average"] is None else f"{g['close_vs_200d_average']:+.1%}"
    pe = "" if g["pe"] is None else f"; P/E {g['pe']:.1f}, at or below it on {g['pe_percentile']:.0%} of days held"
    return (f"{g['day']}: {g['state']}. Volatility {g['realised_vol']:.1%} a year, higher than {g['vol_percentile']:.0%} of the {g['vol_history_days']} days held; "
            f"{g['drawdown_from_252d_high']:.1%} below the 252-day high; {trend} against the 200-day average{pe}.")


def main(argv: Sequence[str] | None = None) -> int:
    from athena.warehouse.warehouse import DEFAULT_PATH, Warehouse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--index", default="Nifty 50")
    parser.add_argument("--db", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)
    with Warehouse(args.db, read_only=True, wait_seconds=120) as warehouse:
        closes = warehouse.index_close(args.index)
        pe = {d: v["pe"] for d, v in warehouse.index_valuation(args.index).items()}
    print(format_gauge(gauge(closes, pe)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
