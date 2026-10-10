import json
from datetime import date

from test_pit_cases import DAYS, builder

from athena.contracts import AthenaError
from athena.evaluation.pit_run import PACKET_FOR, read_rows, report, rows_for, run_cases

AS_OF = DAYS[200]  # 252 trading days later is still inside the 520 bars, so every horizon has an outcome


def reply(signal="bullish", confidence=70, coverage="full"):
    return {"signal": signal, "confidence": confidence, "reasoning": "r", "data_coverage": coverage, "missing": []}


def analysts(calls=None, **overrides):
    def make(name):
        def run(packet):
            if calls is not None:
                calls.append((name, packet["instrument"]))
            return reply()

        return run

    return {**{name: make(name) for name in PACKET_FOR}, **overrides}


def test_each_analyst_run_writes_one_row_per_horizon_with_the_outcome_it_is_scored_against(tmp_path):
    out = tmp_path / "log.jsonl"
    made = run_cases(builder(), analysts(), ["ACME"], [AS_OF], out)
    rows = read_rows(out)
    assert made == 4 and len(rows) == 4 * 3  # four analysts, three horizons
    first = [r for r in rows if r["specialist"] == "quant_technical"]
    assert sorted(r["horizon"] for r in first) == [63, 126, 252] and all(r["label"] in ("outperform", "inline", "underperform") for r in first)
    assert first[0]["symbol"] == "ACME" and first[0]["as_of"] == AS_OF.isoformat() and first[0]["signal"] == "bullish" and first[0]["blinded"] is False


def test_a_run_is_resumed_not_repeated_and_repeats_are_numbered(tmp_path):
    out, calls = tmp_path / "log.jsonl", []
    run_cases(builder(), analysts(calls), ["ACME"], [AS_OF], out, repeats=2)
    assert len(calls) == 8
    again = []
    assert run_cases(builder(), analysts(again), ["ACME"], [AS_OF], out, repeats=2) == 0 and again == []
    assert run_cases(builder(), analysts(again), ["ACME"], [AS_OF], out, repeats=3) == 4 and len(again) == 4
    assert {r["run"] for r in read_rows(out)} == {0, 1, 2}


def test_a_blind_run_hands_the_analysts_packets_without_the_symbol(tmp_path):
    calls = []
    run_cases(builder(), analysts(calls), ["ACME"], [date(2025, 2, 14)], tmp_path / "b.jsonl", blind=True)
    assert {instrument for _, instrument in calls} == {"ASSET"} and read_rows(tmp_path / "b.jsonl")[0]["blinded"] is True


def test_insufficient_data_is_not_a_call_and_a_failing_analyst_is_logged_not_fatal(tmp_path):
    def broken(packet):
        raise AthenaError("model unavailable")

    out = tmp_path / "log.jsonl"
    run_cases(builder(), analysts(valuation=lambda p: reply(coverage="insufficient"), moat_quality=broken), ["ACME"], [AS_OF], out)
    rows = read_rows(out)
    valuation = next(r for r in rows if r["specialist"] == "valuation")
    moat = next(r for r in rows if r["specialist"] == "moat_quality")
    assert valuation["signal"] is None and valuation["coverage"] == "insufficient"
    assert moat["signal"] is None and "model unavailable" in moat["error"]


def test_a_case_whose_outcome_is_not_known_yet_is_skipped_and_said_so(tmp_path):
    messages = []
    assert run_cases(builder(), analysts(), ["ACME"], [DAYS[-5]], tmp_path / "log.jsonl", on_progress=messages.append) == 0
    assert "no outcome is known yet" in messages[0]


def test_a_case_with_no_fundamentals_packet_logs_the_missing_packet_for_those_analysts(tmp_path):
    out = tmp_path / "log.jsonl"
    run_cases(builder(yahoo=False), analysts(), ["ACME"], [date(2026, 3, 31)], out)
    rows = read_rows(out)
    assert all(r["signal"] is None and r["error"] == "no packet for this case" for r in rows if r["specialist"] == "valuation")
    assert all(r["signal"] == "bullish" for r in rows if r["specialist"] == "quant_technical")


def test_the_report_scores_each_analyst_at_each_horizon_and_a_cut_off_log_line_is_skipped(tmp_path):
    out = tmp_path / "log.jsonl"
    run_cases(builder(), analysts(), ["ACME"], [AS_OF], out)
    with out.open("a", encoding="utf-8") as handle:
        handle.write('{"symbol": "AC')
    text = report(read_rows(out))
    assert "quant_technical, 63-day outcome: 1 cases" in text and "earnings_intelligence, 252-day outcome" in text
    assert report([]) == "No scored runs yet."
    assert rows_for(builder().build("ACME", AS_OF), "valuation", 0, reply())[0]["excess"] is not None
    assert json.loads(json.dumps(rows_for(builder().build("ACME", AS_OF), "valuation", 0, None, "x")[0]))["signal"] is None
