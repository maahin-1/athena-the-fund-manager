import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
from bar_factory import make_bars

from athena.evaluation import kronos_test as kt

END = date(2026, 10, 2)


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kronos_forecast.py"
    spec = importlib.util.spec_from_file_location("kronos_forecast", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_export_row_holds_no_bar_after_the_date_and_every_stock_has_the_same_length():
    long, short = make_bars([100 + i for i in range(600)], end=END), make_bars([50 + i for i in range(300)], end=END)
    row = kt.export_row({"LONG": long, "SHORT": short}, date(2026, 6, 30))
    assert row["as_of"] == "2026-06-30" and row["lookback"] == len(row["stocks"]["LONG"]) == len(row["stocks"]["SHORT"])
    assert max(r[0] for stock in row["stocks"].values() for r in stock) <= "2026-06-30"
    assert row["lookback"] <= kt.LOOKBACK
    assert kt.export_row({"S": make_bars([1.0] * 50, end=END)}, END) is None  # too little history


def test_thirds_rank_top_bullish_bottom_bearish_and_ties_do_not_depend_on_order():
    values = {c: float(i) for i, c in enumerate("ABCDEF")}
    assert kt.thirds(values) == {"F": "bullish", "E": "bullish", "D": "neutral", "C": "neutral", "B": "bearish", "A": "bearish"}
    assert kt.thirds({"B": 1.0, "A": 1.0, "C": 0.0, "D": 0.0, "E": 2.0, "F": 2.0}) == kt.thirds({"F": 2.0, "E": 2.0, "D": 0.0, "C": 0.0, "A": 1.0, "B": 1.0})


def test_spearman_is_one_for_the_same_order_minus_one_for_the_opposite_and_none_for_no_spread():
    assert kt.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert kt.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert kt.spearman([1, 1, 1], [1, 2, 3]) is None and kt.spearman([1, 2], [1, 2]) is None


def test_rows_are_logged_for_the_forecaster_and_the_momentum_ranking_on_the_same_outcomes():
    symbols = [f"S{i}" for i in range(6)]
    day = "2025-06-30"
    forecasts = {(s, day): {"ret63": float(i), "ret126": float(i)} for i, s in enumerate(symbols)}
    outcomes = {(s, day, h): ("outperform" if i >= 4 else "underperform" if i < 2 else "inline", i / 10) for i, s in enumerate(symbols) for h in kt.HORIZONS}
    momentum = {(s, day, h): float(-i) for i, s in enumerate(symbols) for h in kt.HORIZONS}  # the opposite order
    rows, ics = kt.score_rows(forecasts, outcomes, momentum)
    kronos = [r for r in rows if r["specialist"] == "kronos" and r["horizon"] == 63]
    assert {r["symbol"]: r["signal"] for r in kronos} == {"S5": "bullish", "S4": "bullish", "S3": "neutral", "S2": "neutral", "S1": "bearish", "S0": "bearish"}
    assert all(r["label"] and r["as_of"] == day for r in rows) and len(rows) == 2 * 2 * 6
    assert ics["kronos"][63] == [pytest.approx(1.0)] and ics["rank_momentum"][63] == [pytest.approx(-1.0)]
    assert "kronos, 63-day: mean +1.000 over 1 dates" in kt.ic_summary(ics)


def test_a_date_with_too_few_known_stocks_is_left_out():
    forecasts = {(f"S{i}", "2025-06-30"): {"ret63": float(i)} for i in range(4)}
    outcomes = {(f"S{i}", "2025-06-30", 63): ("inline", 0.0) for i in range(4)}
    assert kt.score_rows(forecasts, outcomes, {})[0] == []


def test_the_forecast_script_reads_returns_off_the_path_and_resumes(tmp_path):
    script = load_script()
    closes = [100.0 + i for i in range(126)]
    assert script.predicted_returns(closes, 100.0) == {"ret63": pytest.approx(0.62), "ret126": pytest.approx(1.25)}
    assert script.predicted_returns(closes[:70], 100.0).keys() == {"ret63"}
    out = tmp_path / "f.jsonl"
    out.write_text(json.dumps({"as_of": "2025-01-31", "symbol": "A"}) + "\nnot json\n", encoding="utf-8")
    assert script.done_dates(out) == {"2025-01-31"} and script.done_dates(tmp_path / "none") == set()
