import json

from athena.verdicts.calibration import UNCALIBRATED_CAP, UNMEASURED_TRUST, Calibration, Measure, dampen


def rows(analyst, horizon, calls):
    """calls: list of (signal, label). Each is its own case."""
    return [
        {"symbol": f"S{i}", "as_of": "2024-01-31", "specialist": analyst, "run": 0, "signal": signal, "confidence": 80, "horizon": horizon, "label": label}
        for i, (signal, label) in enumerate(calls)
    ]


def verdict(conviction=80):
    return {"verdict": "Buy", "conviction": conviction, "key_risks": ["x"], "resolution_path": "blend"}


def test_trust_is_accuracy_over_the_best_fixed_answer_and_unmeasured_is_half():
    log = rows("valuation", 63, [("bullish", "outperform"), ("bullish", "outperform"), ("bearish", "outperform"), ("neutral", "underperform")])
    cal = Calibration.from_log_rows(log)
    m = cal.measures["valuation"][63]
    assert m.cases == 4 and m.accuracy == 0.5 and m.best_fixed == 0.75
    assert round(cal.trust("valuation"), 3) == round(0.5 / 0.75, 3)
    assert cal.trust("nobody") == UNMEASURED_TRUST


def test_nobody_is_calibrated_with_few_cases_or_no_edge():
    cal = Calibration({"a": {63: Measure(10, 0.9, 0.4)}, "b": {63: Measure(400, 0.41, 0.4)}, "c": {63: Measure(400, 0.5, 0.4)}})
    assert not cal.calibrated(["a"]) and not cal.calibrated(["b"]) and cal.calibrated(["c"])
    assert not cal.calibrated(["c", "a"]) and not cal.calibrated([]) and not cal.calibrated(["unknown"])


def test_uncalibrated_conviction_is_capped_and_scaled_and_the_band_is_unchanged():
    out = dampen(verdict(80), ["quant_technical"], Calibration())
    assert out["verdict"] == "Buy" and out["raw_conviction"] == 80
    assert out["conviction"] == round(UNCALIBRATED_CAP * UNMEASURED_TRUST) and out["calibration"] == "uncalibrated"
    assert "dampened" in out["key_risks"][0] and out["key_risks"][1] == "x"


def test_a_low_conviction_is_scaled_not_raised_to_the_cap():
    assert dampen(verdict(20), ["a"], Calibration())["conviction"] == 10


def test_a_calibrated_verdict_keeps_its_conviction():
    cal = Calibration({"a": {63: Measure(400, 0.6, 0.4)}})
    out = dampen(verdict(80), ["a"], cal)
    assert out["conviction"] == 80 and out["calibration"] == "calibrated"


def test_no_participants_is_uncalibrated_with_no_trust():
    out = dampen({"verdict": "Hold", "conviction": 0, "key_risks": []}, [], Calibration())
    assert out["conviction"] == 0 and out["trust"] is None and out["calibration"] == "uncalibrated"


def test_save_and_load_round_trip_and_a_missing_file_is_empty(tmp_path):
    cal = Calibration({"a": {63: Measure(5, 0.5, 0.6)}})
    cal.save(tmp_path / "c.json")
    assert Calibration.load(tmp_path / "c.json") == cal
    assert Calibration.load(tmp_path / "none.json").measures == {}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert Calibration.load(tmp_path / "bad.json").measures == {}


def test_logs_are_read_from_json_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows("valuation", 63, [("bullish", "outperform")])) + "\n", encoding="utf-8")
    assert Calibration.from_logs([path]).measures["valuation"][63].cases == 1
