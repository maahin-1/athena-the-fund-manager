from datetime import date, datetime, timedelta, timezone

import pytest

from athena.metrics.packets import RISKFREE_NAME, TRACKING_NOTE, build_packet

UTC = timezone.utc
AS_OF = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
START = date(2026, 1, 1)


def days(n):
    out, d = [], START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def levels(dates, daily_returns, start=100.0):
    out, level = {}, start
    for d, r in zip(dates, [0.0] + daily_returns):
        level *= 1.0 + r
        out[d] = level
    return out


N = 120
DATES = days(N)
BENCH_R = [((i * 7) % 11 - 5) / 500 for i in range(N - 1)]
ASSET_R = [1.5 * r + 0.0003 for r in BENCH_R]
INDEX_R = [r + (0.0005 if i % 2 else -0.0005) for i, r in enumerate(BENCH_R)]
BENCH = levels(DATES, BENCH_R)
ASSET = levels(DATES, ASSET_R)
RATE = levels(DATES, [0.0002] * (N - 1), start=2600.0)
TRACKED = levels(DATES, INDEX_R)


def full_packet(**overrides):
    args = dict(
        instrument="SBIN", as_of=AS_OF, asset=ASSET, benchmark=BENCH, benchmark_name="NIFTY 50",
        riskfree=RATE, tracking_index=TRACKED, tracking_index_name="NIFTY 50 TRI",
    )
    args.update(overrides)
    return build_packet(**args)


def test_full_packet_has_every_metric_and_nothing_missing():
    packet = full_packet()
    assert packet["instrument"] == "SBIN"
    assert packet["as_of"] == AS_OF.isoformat()
    assert set(packet["metrics"]) == {
        "volatility_annualized", "max_drawdown", "beta", "alpha_annualized",
        "sharpe", "tracking_error", "tracking_difference",
    }
    assert packet["missing"] == [] and packet["missing_reasons"] == {}


def test_values_match_the_construction_of_the_series():
    metrics = full_packet()["metrics"]
    assert metrics["beta"]["value"] == pytest.approx(1.5, abs=1e-4)
    assert metrics["alpha_annualized"]["value"] == pytest.approx(0.0004 * 252, abs=1e-3)
    assert metrics["beta"]["unit"] == "ratio" and metrics["volatility_annualized"]["unit"] == "fraction"
    assert metrics["beta"]["inputs"] == ["asset", "NIFTY 50"]
    assert metrics["alpha_annualized"]["inputs"] == ["asset", "NIFTY 50", RISKFREE_NAME]
    assert metrics["max_drawdown"]["value"] <= 0.0
    assert metrics["tracking_error"]["value"] > 0.0
    assert metrics["tracking_error"]["note"] == TRACKING_NOTE
    assert "note" not in metrics["beta"]


def test_window_text_reports_the_returns_used_and_the_date_range():
    metric = full_packet(window=61)["metrics"]["volatility_annualized"]
    assert metric["window"] == f"60 returns {DATES[-61]}..{DATES[-1]}"


def test_missing_benchmark_marks_beta_and_alpha_missing_with_reasons():
    packet = full_packet(benchmark=None)
    assert {"beta", "alpha_annualized"} <= set(packet["missing"])
    assert "beta" not in packet["metrics"] and "sharpe" in packet["metrics"]
    assert packet["missing_reasons"]["beta"] == "required series not provided"


def test_missing_riskfree_marks_alpha_and_sharpe_missing():
    packet = full_packet(riskfree=None)
    assert {"alpha_annualized", "sharpe"} <= set(packet["missing"])
    assert "beta" in packet["metrics"]


def test_riskfree_index_that_starts_late_shrinks_the_window_to_the_overlap():
    late_rate = {d: v for d, v in RATE.items() if d >= DATES[60]}
    packet = full_packet(riskfree=late_rate)
    assert "sharpe" in packet["metrics"]
    assert packet["metrics"]["sharpe"]["window"].startswith("59 returns")


def test_riskfree_index_with_too_little_overlap_marks_alpha_and_sharpe_missing():
    barely = {d: v for d, v in RATE.items() if d >= DATES[100]}
    packet = full_packet(riskfree=barely)
    assert {"alpha_annualized", "sharpe"} <= set(packet["missing"])
    assert "at least 20" in packet["missing_reasons"]["sharpe"]


def test_no_tracking_index_marks_tracking_metrics_missing():
    packet = full_packet(tracking_index=None)
    assert {"tracking_error", "tracking_difference"} <= set(packet["missing"])


def test_short_history_marks_metrics_missing_instead_of_raising():
    short = {d: v for d, v in list(ASSET.items())[:10]}
    packet = full_packet(asset=short)
    assert "volatility_annualized" in packet["missing"]
    assert "at least 20" in packet["missing_reasons"]["volatility_annualized"]
    assert "tracking_error" in packet["missing"]
    assert "tracking_difference" in packet["metrics"]


def test_packet_is_json_serialisable():
    import json

    json.dumps(full_packet())
