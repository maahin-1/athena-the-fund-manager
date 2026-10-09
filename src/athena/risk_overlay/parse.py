from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from athena.resolver import normalize_input
from athena.risk_overlay.model import MAX_HOLDINGS, MEASURES, PRESETS, Holding, Limit, Overlay, RiskProfile

NAME_LIMIT = 60
PROFILE_KEYS = {"preset", "name", "limits"}
HOLDINGS_HEADER = ("symbol", "value")


class ProfileError(ValueError):
    """A profile or a holdings list that cannot be used; the message says which part and why."""


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{path}: must be a number")
    try:
        number = float(value)  # a huge integer overflows here
    except OverflowError:
        raise ProfileError(f"{path}: is too large") from None
    if not math.isfinite(number) or number <= 0:
        raise ProfileError(f"{path}: must be a number above zero")
    return number


def _limit(data: Any, path: str) -> Limit:
    if not isinstance(data, dict) or set(data) != {"warn", "hard"}:
        raise ProfileError(f"{path}: must be an object with exactly 'warn' and 'hard' (or null to switch the check off)")
    warn, hard = _number(data["warn"], f"{path}.warn"), _number(data["hard"], f"{path}.hard")
    if warn >= hard:
        raise ProfileError(f"{path}: 'warn' must be below 'hard'")
    return Limit(warn, hard)


def _parse_profile(data: Any) -> RiskProfile:
    if not isinstance(data, dict):
        raise ProfileError("a profile must be an object")
    extra = sorted(str(key) for key in set(data) - PROFILE_KEYS)
    if extra:
        raise ProfileError(f"unexpected key(s) {extra}; allowed: {sorted(PROFILE_KEYS)}")
    preset = data.get("preset")
    if preset is not None and (not isinstance(preset, str) or preset not in PRESETS):
        raise ProfileError(f"preset: unknown preset {preset!r}; choose from {', '.join(PRESETS)}")
    name = data.get("name", preset or "custom")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= NAME_LIMIT:
        raise ProfileError(f"name: must be text of 1 to {NAME_LIMIT} characters")
    limits = dict(PRESETS[preset].limits) if preset else {}
    given = data.get("limits", {})
    if not isinstance(given, dict):
        raise ProfileError("limits: must be an object")
    for measure, raw in given.items():
        if not isinstance(measure, str) or measure not in MEASURES:
            raise ProfileError(f"limits.{measure}: unknown check; choose from {', '.join(MEASURES)}")
        if raw is None:
            limits.pop(measure, None)
        else:
            limits[measure] = _limit(raw, f"limits.{measure}")
    if not limits:
        raise ProfileError("a profile needs at least one limit (give a preset or some limits)")
    return RiskProfile(name.strip(), {measure: limits[measure] for measure in MEASURES if measure in limits})


def parse_profile(data: Any) -> RiskProfile:
    """A profile from plain data; raises ProfileError, never anything else, whatever the data holds."""
    try:
        return _parse_profile(data)
    except ProfileError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ProfileError("this is not a valid profile") from None


def profile_to_dict(profile: RiskProfile) -> dict:
    return {"name": profile.name, "limits": {m: {"warn": limit.warn, "hard": limit.hard} for m, limit in profile.limits.items()}}


def parse_holdings(text: str) -> tuple[Holding, ...]:
    """Holdings from CSV text with a `symbol,value` header; the same symbol on several rows is added up."""
    try:
        return _parse_holdings(text)
    except ProfileError:
        raise
    except (csv.Error, TypeError, ValueError, OverflowError):
        raise ProfileError("holdings: this is not a readable CSV with 'symbol,value' columns") from None


def _parse_holdings(text: str) -> tuple[Holding, ...]:
    if not text.strip():
        return ()
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = [cell.strip().lower() for cell in next(reader, [])]
    if tuple(header) != HOLDINGS_HEADER:
        raise ProfileError("holdings: the first row must be the header 'symbol,value'")
    totals: dict[str, float] = {}
    for number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != 2:
            raise ProfileError(f"holdings row {number}: expected 2 columns (symbol,value), got {len(row)}")
        symbol = normalize_input(row[0])
        if not symbol:
            raise ProfileError(f"holdings row {number}: the symbol is empty")
        try:
            value = float(row[1].strip().replace(",", ""))
        except ValueError:
            raise ProfileError(f"holdings row {number}: {row[1]!r} is not a number") from None
        if not math.isfinite(value) or value <= 0:
            raise ProfileError(f"holdings row {number}: the value must be a number above zero")
        totals[symbol] = totals.get(symbol, 0.0) + value
        if len(totals) > MAX_HOLDINGS:
            raise ProfileError(f"holdings: at most {MAX_HOLDINGS} different symbols")
    return tuple(Holding(symbol, value) for symbol, value in totals.items())


def overlay_to_dict(overlay: Overlay) -> dict:
    """Plain data that identifies an overlay request, for caching and for tests."""
    return {
        "profile": profile_to_dict(overlay.profile),
        "holdings": [[h.symbol, h.value] for h in overlay.holdings],
        "amount": overlay.amount,
    }


def overlay_token(overlay: Overlay | None) -> str:
    return "none" if overlay is None else json.dumps(overlay_to_dict(overlay), sort_keys=True)


def load_profile(spec: str) -> RiskProfile:
    """A preset name, or the path of a JSON file holding a profile."""
    if spec in PRESETS:
        return PRESETS[spec]
    try:
        text = Path(spec).read_text(encoding="utf-8")
    except OSError:
        raise ProfileError(f"profile: {spec!r} is not a preset ({', '.join(PRESETS)}) and cannot be read as a file") from None
    except UnicodeDecodeError:
        raise ProfileError(f"profile: {spec} is not a text file") from None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise ProfileError(f"profile: {spec} is not valid JSON") from None
    return parse_profile(data)


def load_holdings(path: str) -> tuple[Holding, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ProfileError(f"holdings: cannot read {path}") from None
    except UnicodeDecodeError:
        raise ProfileError(f"holdings: {path} is not a text file") from None
    return parse_holdings(text)


def build_overlay(profile: str | None, holdings: str | None, amount: float | None) -> Overlay | None:
    """The overlay the command line asks for, or None when no profile was given."""
    if profile is None:
        if holdings is not None or amount is not None:
            raise ProfileError("--holdings and --amount need --profile")
        return None
    if amount is not None and (not math.isfinite(amount) or amount <= 0):
        raise ProfileError("--amount must be a number above zero")
    return Overlay(load_profile(profile), load_holdings(holdings) if holdings else (), amount)
