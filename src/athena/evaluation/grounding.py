from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# A number as written in prose: optional sign, digits with thousands commas, optional decimals, optional %.
# Digits glued to letters ("FY2026", "Q3") are not numbers.
_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_NUMBER_IN_DATA_STRING = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class GroundingResult:
    checked: int
    ungrounded: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.ungrounded


def numbers_in_text(text: str) -> list[str]:
    return _NUMBER_IN_TEXT.findall(text)


def collect_values(data: Any) -> list[float]:
    """Every number reachable in `data`, including numbers written inside strings (windows, dates)."""
    values: list[float] = []
    if isinstance(data, bool):
        return values
    if isinstance(data, (int, float)):
        values.append(float(data))
    elif isinstance(data, str):
        values.extend(float(match) for match in _NUMBER_IN_DATA_STRING.findall(data))
    elif isinstance(data, Mapping):
        for item in data.values():
            values.extend(collect_values(item))
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            values.extend(collect_values(item))
    return values


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _matches(token: str, values: list[float]) -> bool:
    cleaned = token.replace(",", "").rstrip("%")
    cited = abs(float(cleaned))
    tolerance = 0.5 * 10 ** (-_decimals(cleaned)) + 1e-9
    for value in values:
        for candidate in (value, value * 100.0):  # a fraction may be cited as a percentage
            if abs(abs(candidate) - cited) <= tolerance:
                return True
    return False


def check_grounded(reasoning: str, data: Any, ignore_small_integers: bool = True) -> GroundingResult:
    """Every figure cited in `reasoning` must appear in `data` (within rounding to the digits cited).

    Plain integers 0-10 (counts such as "3 years") are ignored unless `ignore_small_integers` is False.
    A negative value may be cited by its magnitude ("23.5% drawdown")."""
    values = collect_values(data)
    checked = 0
    ungrounded: list[str] = []
    for token in numbers_in_text(reasoning):
        bare = token.replace(",", "").lstrip("+-")
        if ignore_small_integers and bare.isdigit() and int(bare) <= 10:
            continue
        checked += 1
        if not _matches(token, values):
            ungrounded.append(token)
    return GroundingResult(checked, tuple(ungrounded))
