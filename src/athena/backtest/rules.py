from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from athena.technicals.candles import Candle

ENTRY, EXIT = "enter", "exit"
ALIGNED_UP = "aligned_up"
MIN_REWARD_RISK = 2.0  # the persona's minimum reward:risk (TRD 2.3)


def _value(packet: Mapping[str, Any], name: str) -> Any:
    metric = packet["metrics"].get(name)
    return None if metric is None else metric["value"]


@dataclass(frozen=True)
class Rule:
    """A long-only decision rule over a technical packet (the same packet the Quant/Technical specialist reads).
    `enter` is asked while flat, `leave` while long; a missing figure never triggers either."""

    name: str
    description: str
    enter: Callable[[Mapping[str, Any]], bool]
    leave: Callable[[Mapping[str, Any]], bool]


class Signals(Protocol):
    def decide(self, index: int, holding: bool) -> tuple[str | None, float | None]:
        """ENTRY, EXIT or None after the close of bar `index`, and the ATR to size a protective stop with (or None)."""


@dataclass(frozen=True)
class SeriesRule:
    """A long-only rule computed once over the whole history and read at each close. `prepare` must build signals in
    which the value at bar t uses only bars up to t (every indicator and rolling window the strategy format allows is
    causal), so reading index t is as safe as rebuilding the packet from the bars up to t. `stop_atr` is the
    protective stop in ATR multiples (None for no stop) and replaces the run's default."""

    name: str
    description: str
    prepare: Callable[[Sequence[Candle]], Signals]
    stop_atr: float | None = None


def _trend_lost(packet: Mapping[str, Any]) -> bool:
    alignment = _value(packet, "trend_alignment")
    return alignment is not None and alignment != ALIGNED_UP


def _trend_entry(packet: Mapping[str, Any]) -> bool:
    return _value(packet, "trend_alignment") == ALIGNED_UP


def _persona_entry(packet: Mapping[str, Any]) -> bool:
    volume = _value(packet, "volume_ratio_20_50")
    reward_risk = _value(packet, "reward_risk")
    return (
        _trend_entry(packet)
        and volume is not None and volume > 1.0
        and reward_risk is not None and reward_risk >= MIN_REWARD_RISK
    )


TREND = Rule(
    "trend",
    "own it while the daily, weekly and monthly trends are all up; leave when they are no longer aligned",
    _trend_entry,
    _trend_lost,
)
PERSONA = Rule(
    "persona",
    "the Quant/Technical persona's long setup: all three trends up, volume ratio above 1 and reward:risk of at least 2 "
    "(a pullback with room back to the 60-day high); leave when the trends stop being aligned",
    _persona_entry,
    _trend_lost,
)
RULES: dict[str, Rule] = {rule.name: rule for rule in (TREND, PERSONA)}
