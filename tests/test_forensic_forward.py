from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from athena.contracts import AthenaError
from athena.forensic.statements import forward


def fake_universe() -> SimpleNamespace:
    def finding(key: str, elevated: bool) -> SimpleNamespace:
        return SimpleNamespace(key=key, elevated=elevated, value=1.234567, worse_than=0.95 if elevated else 0.2, peers=40)

    findings = {"AAA": [finding("cfo_to_profit", True), finding("accruals_ratio", False)], "BBB": []}
    return SimpleNamespace(
        annuals={"AAA": SimpleNamespace(industry="Steel"), "BBB": SimpleNamespace(industry="")},
        values={"AAA": {"cfo_to_profit": 1, "accruals_ratio": 1}, "BBB": {}},
        findings=lambda s: findings[s],
    )


def take(tmp_path: Path, as_of: date = date(2026, 11, 6)) -> Path:
    return forward.snapshot(as_of, root=tmp_path, universe=fake_universe(), now=datetime(2026, 11, 6, tzinfo=timezone.utc))


def test_snapshot_freezes_measures_ranks_and_a_hash(tmp_path: Path) -> None:
    path = take(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["as_of"] == "2026-11-06" and record["companies"] == 2
    aaa = next(r for r in record["rows"] if r["symbol"] == "AAA")
    assert aaa["elevated"] == ["cfo_to_profit"] and aaa["computed"] == ["accruals_ratio", "cfo_to_profit"]
    assert aaa["ranked"]["cfo_to_profit"]["worse_than"] == 0.95
    assert forward.verify(path)["hash_ok"]


def test_a_date_is_never_snapshotted_twice(tmp_path: Path) -> None:
    take(tmp_path)
    with pytest.raises(AthenaError, match="never re-taken"):
        take(tmp_path)


def test_a_changed_frozen_figure_is_found(tmp_path: Path) -> None:
    path = take(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["rows"][0]["elevated"] = []
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AthenaError, match="recorded hash"):
        forward.verify(path)


def test_grading_waits_twelve_months_whatever_the_window_end(tmp_path: Path) -> None:
    path = take(tmp_path)
    with pytest.raises(AthenaError, match="no earlier than 2027-11-06"):
        forward.grade(path, date(2027, 11, 6), today=date(2027, 6, 1))  # too early today
    with pytest.raises(AthenaError, match="no earlier than 2027-11-06"):
        forward.grade(path, date(2027, 6, 1), today=date(2027, 12, 1))  # window ends too early


def test_drift_reports_companies_whose_elevated_set_changed(tmp_path: Path) -> None:
    record = forward.load(take(tmp_path))
    same = forward.drift(record, fake_universe())
    assert same["changed_elevated_set"] == 0 and same["new_since"] == 0
    later = fake_universe()
    later.findings = lambda s: [SimpleNamespace(key="accruals_ratio", elevated=True, value=1, worse_than=0.9, peers=40)] if s == "AAA" else []
    later.annuals["CCC"] = SimpleNamespace(industry="")
    later_findings = later.findings
    later.findings = lambda s: later_findings(s) if s != "CCC" else []
    d = forward.drift(record, later)
    assert d["changed_elevated_set"] == 1 and d["examples"] == ["AAA"] and d["new_since"] == 1
