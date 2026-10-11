"""How far to trust an analyst's confidence, from how it actually did.

The Phase 1k point-in-time runs scored each analyst against what the stock then did. This module turns those scores into a trust
figure per analyst and uses it to dampen the conviction a verdict shows. A verdict counts as calibrated only when every analyst
that took part has enough measured cases and beats the best fixed answer by a margin at each horizon; until then the number is
capped and scaled and says so."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from athena.evaluation.pit_score import calls_from_rows, score

CALIBRATION_PATH = Path(".athena") / "calibration.json"
UNCALIBRATED_CAP = 50  # the most conviction a verdict shows before it is calibrated
UNMEASURED_TRUST = 0.5  # trust in an analyst nobody has measured
MIN_CASES = 300  # measured cases per horizon before an analyst can count as calibrated
MIN_EDGE = 0.05  # accuracy above the best fixed answer needed at every horizon
UNCALIBRATED, CALIBRATED = "uncalibrated", "calibrated"


@dataclass(frozen=True)
class Measure:
    cases: int
    accuracy: float  # share of calls matching the outcome
    best_fixed: float  # accuracy of always giving the most common outcome


@dataclass(frozen=True)
class Calibration:
    measures: Mapping[str, Mapping[int, Measure]] = field(default_factory=dict)  # analyst -> horizon -> measure

    def trust(self, analyst: str) -> float:
        """Measured accuracy over the best fixed answer's, averaged over the horizons, at most 1."""
        per_horizon = self.measures.get(analyst)
        if not per_horizon:
            return UNMEASURED_TRUST
        ratios = [min(1.0, m.accuracy / m.best_fixed) if m.best_fixed else UNMEASURED_TRUST for m in per_horizon.values()]
        return sum(ratios) / len(ratios)

    def calibrated(self, analysts: Iterable[str]) -> bool:
        names = list(analysts)
        if not names:
            return False
        for name in names:
            per_horizon = self.measures.get(name)
            if not per_horizon:
                return False
            if any(m.cases < MIN_CASES or m.accuracy < m.best_fixed + MIN_EDGE for m in per_horizon.values()):
                return False
        return True

    def to_json(self) -> dict[str, Any]:
        return {a: {str(h): vars(m) for h, m in per.items()} for a, per in self.measures.items()}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Calibration":
        return cls({a: {int(h): Measure(int(m["cases"]), float(m["accuracy"]), float(m["best_fixed"])) for h, m in per.items()} for a, per in data.items()})

    @classmethod
    def from_log_rows(cls, rows: Iterable[Mapping[str, Any]]) -> "Calibration":
        calls = calls_from_rows(rows)
        measures: dict[str, dict[int, Measure]] = {}
        for analyst in sorted({c.specialist for c in calls}):
            for horizon in sorted({c.horizon for c in calls if c.specialist == analyst}):
                result = score(calls, analyst, horizon)
                if result is None or not result.cases:
                    continue
                measures.setdefault(analyst, {})[horizon] = Measure(result.cases, result.accuracy, max(result.outcome_mix.values()))
        return cls(measures)

    @classmethod
    def from_logs(cls, paths: Iterable[Path]) -> "Calibration":
        rows = [json.loads(line) for path in paths for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        return cls.from_log_rows(rows)

    def save(self, path: Path = CALIBRATION_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CALIBRATION_PATH) -> "Calibration":
        """The saved calibration, or an empty one (every analyst unmeasured) when there is none or it cannot be read."""
        try:
            return cls.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return cls()


def dampen(verdict: Mapping[str, Any], taking_part: Iterable[str], calibration: Calibration) -> dict[str, Any]:
    """The verdict with its conviction dampened. The band is untouched; the raw conviction is kept beside the shown one."""
    names = list(taking_part)
    raw = int(verdict["conviction"])
    out = dict(verdict)
    out["raw_conviction"] = raw
    if not names:
        out.update(calibration=UNCALIBRATED, trust=None)
        return out
    trust = sum(calibration.trust(n) for n in names) / len(names)
    if calibration.calibrated(names):
        out.update(conviction=raw, calibration=CALIBRATED, trust=round(trust, 3))
        return out
    shown = round(min(raw, UNCALIBRATED_CAP) * trust)
    out.update(conviction=shown, calibration=UNCALIBRATED, trust=round(trust, 3))
    out["key_risks"] = [
        f"Conviction is dampened ({raw} before, {shown} shown): the analysts' confidence has not been calibrated against outcomes "
        f"(capped at {UNCALIBRATED_CAP}, scaled by measured trust {trust:.2f}).",
        *verdict.get("key_risks", []),
    ]
    return out
