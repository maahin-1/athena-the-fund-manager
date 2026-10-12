import json
from datetime import date, datetime, timedelta, timezone

from athena.verdicts import cli
from athena.verdicts.calibration import Calibration, Measure
from athena.warehouse.warehouse import Warehouse

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def stored():
    verdict = {"verdict": "Buy", "conviction": 20, "raw_conviction": 70, "calibration": "uncalibrated", "key_risks": ["a", "b"]}
    payload = {
        "verdict": verdict, "why": "asked for",
        "specialists": {"valuation": {"signal": "bullish", "confidence": 70, "data_coverage": "full"}},
        "skipped": {"news_sentiment": "not built yet"},
    }
    return {"symbol": "TCS", "made_at": NOW, "data_as_of": date(2026, 10, 9), "payload": payload}


def test_a_verdict_is_described_with_its_analysts_and_what_did_not_run():
    text = cli.describe(stored())
    assert "TCS: Buy, conviction 20 (uncalibrated; raw 70)" in text and "valuation: bullish 70 (full)" in text
    assert "news_sentiment: not run, not built yet" in text and "risk: a" in text


def test_the_calibration_is_listed_per_analyst():
    text = cli.format_calibration(Calibration({"valuation": {63: Measure(100, 0.4, 0.5)}}))
    assert "valuation: trust 0.10" in text and "63d: 40% vs 50% fixed over 100" in text
    assert cli.format_calibration(Calibration()) == "nothing measured"


def test_the_report_names_what_was_made_and_what_failed(tmp_path):
    summary = {"checked": 5, "changed": ["A", "B"], "made": ["A"], "failed": ["B: down"], "left": 0, "stopped": "3 failures in a row"}
    path = cli.write_report(summary, NOW, NOW + timedelta(minutes=2), tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "5 stocks with a verdict checked, 2 out of date, 1 made again" in text and "Stopped early: 3 failures in a row" in text and "FAILED B: down" in text
    assert (tmp_path / "verdicts_latest.md").read_text(encoding="utf-8") == text


def test_calibrate_reads_the_logs_and_saves_the_calibration(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "log.jsonl"
    row = {"symbol": "S", "as_of": "2024-01-31", "specialist": "valuation", "run": 0, "signal": "bullish", "confidence": 80, "horizon": 63, "label": "outperform"}
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert cli.main(["--calibrate", str(log)]) == 0
    assert "valuation" in capsys.readouterr().out
    assert Calibration.load(tmp_path / ".athena" / "calibration.json").measures["valuation"][63].cases == 1


def test_list_shows_each_stock_with_a_verdict_and_whether_it_is_out_of_date(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "w.duckdb"
    with Warehouse(path) as w:
        w.set_universe([("TCS", "stock", "TCS Ltd", "i")], NOW)
        w.put_packets([("TCS", "technical", date(2026, 10, 9), [], {"metrics": {"last_close": {"value": 70.0}}})], NOW)
        w.put_verdict("TCS", NOW, date(2026, 10, 9), 100.0, None, None, {"verdict": {"verdict": "Hold"}})
    assert cli.main(["--db", str(path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "TCS: Hold" in out and "out of date: the price has moved -30% since" in out and "1 stocks with a verdict" in out
