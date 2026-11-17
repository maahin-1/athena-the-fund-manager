"""Verdict settlement (WP02, Q1): a decision is scored from the next session's open after its completion date, never from a price that came before it existed."""
import copy
from datetime import date, datetime, timedelta, timezone

import pytest

from athena import timing
from athena.verdicts import decisions

UTC = timezone.utc
# Wednesday 7 January 2026 is the decision day. Sessions: Mon 5 Jan ... with Monday 12 January a holiday.
SESSIONS = [date(2026, 1, 1) + timedelta(days=i) for i in range(0, 40) if (date(2026, 1, 1) + timedelta(days=i)).weekday() < 5 and date(2026, 1, 1) + timedelta(days=i) != date(2026, 1, 12)]
# the reviewer's reproduction: closes 100, 110, 120, ... one per session; the open of a day is the close of the day before plus 1
CLOSES = {d: 100.0 + 10.0 * i for i, d in enumerate(SESSIONS)}
OPENS = {d: (CLOSES[SESSIONS[i - 1]] + 1.0 if i else 100.0) for i, d in enumerate(SESSIONS)}
STOCK = [(d, OPENS[d], CLOSES[d]) for d in SESSIONS]
BENCH = {d: (1000.0 + i, 1000.0 + i + 0.5) for i, d in enumerate(SESSIONS)}


def test_the_old_data_date_window_ends_before_the_verdict_existed_which_is_the_defect():
    made_at, data_as_of = datetime(2026, 1, 7, 6, 0, tzinfo=UTC), date(2026, 1, 1)
    closes = [(d, c) for d, _, c in STOCK]
    legacy = decisions.settle_one(data_as_of, closes, {d: b[1] for d, b in BENCH.items()}, horizons=(3,))
    assert legacy[3]["stock"] == pytest.approx((CLOSES[SESSIONS[3 + 0]] / CLOSES[date(2026, 1, 1)]) - 1)  # +30%: the 1st to the 6th
    end_of_window = [d for d in SESSIONS if d > data_as_of][2]
    assert end_of_window < timing.decision_date(made_at)  # it ended on 6 January; the decision was completed on the 7th


# The exchange's sessions are established independently of any price download: weekdays that are not recorded holidays, up to the last day the store has examined.
CAL = timing.SessionCalendar(holidays=frozenset({date(2026, 1, 12)}), through=SESSIONS[-1])


def settled(h=5, completed=datetime(2026, 1, 7, 6, 0, tzinfo=UTC), stock=STOCK, bench=BENCH, horizons=(5,), calendar=CAL):
    return decisions.settle_prospective(completed, stock, bench, horizons, calendar=calendar)


def test_entry_is_the_next_sessions_open_after_the_local_decision_date():
    got = settled()
    assert got["policy"] == timing.ENTRY_POLICY and got["decision_date"] == "2026-01-07" and got["entry_session"] == "2026-01-08"
    row = got["horizons"][5]
    assert row["status"] == "settled" and row["entry_date"] == "2026-01-08" and row["entry_price"] == OPENS[date(2026, 1, 8)]
    assert row["exit_date"] == str([d for d in SESSIONS if d >= date(2026, 1, 8)][4])  # the entry session is the first of the five
    assert row["stock"] == pytest.approx(CLOSES[date.fromisoformat(row["exit_date"])] / OPENS[date(2026, 1, 8)] - 1)
    assert date.fromisoformat(row["entry_date"]) > date(2026, 1, 7)  # nothing is earned before the decision existed


def test_a_decision_completed_after_midnight_india_time_enters_a_day_later():
    late = datetime(2026, 1, 7, 20, 0, tzinfo=UTC)  # 01:30 IST on the 8th
    assert settled(completed=late)["entry_session"] == "2026-01-09"


def test_a_friday_decision_enters_on_the_next_session_after_the_holiday_gap():
    assert settled(completed=datetime(2026, 1, 9, 12, 0, tzinfo=UTC))["entry_session"] == "2026-01-13"  # Monday the 12th had no session


def test_the_benchmark_is_measured_over_the_same_interval_from_its_own_open():
    row = settled()["horizons"][5]
    entry, exit_ = date.fromisoformat(row["entry_date"]), date.fromisoformat(row["exit_date"])
    assert row["benchmark"] == pytest.approx(BENCH[exit_][1] / BENCH[entry][0] - 1) and row["excess"] == pytest.approx(row["stock"] - row["benchmark"])


def test_a_missing_benchmark_open_is_a_named_gap_not_a_substituted_close():
    bench = dict(BENCH)
    bench[date(2026, 1, 8)] = (None, 1008.5)
    row = settled(bench=bench)["horizons"][5]
    assert row["status"] == "gap" and "benchmark open" in row["reason"] and "excess" not in row


def test_a_missing_stock_open_or_exit_bar_is_a_gap_and_a_suspended_exit_is_not_filled_with_an_old_close():
    no_open = [(d, None if d == date(2026, 1, 8) else o, c) for d, o, c in STOCK]
    assert settled(stock=no_open)["horizons"][5]["status"] == "gap" and "no open" in settled(stock=no_open)["horizons"][5]["reason"]
    exit_day = [d for d in SESSIONS if d >= date(2026, 1, 8)][4]
    suspended = [(d, o, c) for d, o, c in STOCK if d != exit_day]
    row = settled(stock=suspended)["horizons"][5]
    assert row["status"] == "gap" and "no close" in row["reason"]


def test_a_horizon_not_yet_reached_is_pending_and_no_session_yet_is_pending():
    got = decisions.settle_prospective(datetime(2026, 1, 7, 6, 0, tzinfo=UTC), STOCK, BENCH, (5, 60), calendar=CAL)
    assert got["horizons"][5]["status"] == "settled" and got["horizons"][60]["status"] == "pending" and got["status"] == "partial"
    after_all = decisions.settle_prospective(datetime(2026, 3, 1, 6, 0, tzinfo=UTC), STOCK, BENCH, (5,), calendar=CAL)
    assert after_all["entry_session"] is None and after_all["status"] == "pending"


def test_a_naive_decision_time_cannot_be_settled():
    with pytest.raises(ValueError):
        decisions.settle_prospective(datetime(2026, 1, 7, 6, 0), STOCK, BENCH, (5,), calendar=CAL)


# --- batch-2 review B2-3: the sessions come from the calendar, never from which prices happened to download -----------------------------------------------------------------------------------
def dates_of(result, h=5):
    row = result["horizons"][h]
    return result["entry_session"], row.get("entry_date"), row.get("exit_date")


BASELINE = dates_of(settled())


def test_a_missing_whole_benchmark_entry_row_is_a_gap_and_the_entry_does_not_move_to_a_later_observed_day():
    bench = {d: v for d, v in BENCH.items() if d != date(2026, 1, 8)}  # the intended entry session's benchmark row is simply absent
    got = settled(bench=bench)
    assert got["entry_session"] == "2026-01-08" and dates_of(got) == BASELINE
    row = got["horizons"][5]
    assert row["status"] == "gap" and "benchmark" in row["reason"] and "no row" in row["reason"] and "excess" not in row and got["status"] == "gap"


def test_missing_interior_and_exit_benchmark_rows_do_not_extend_the_holding_window():
    interior = [d for d in SESSIONS if d > date(2026, 1, 8)][1]
    got = settled(bench={d: v for d, v in BENCH.items() if d != interior})
    assert dates_of(got) == BASELINE and got["horizons"][5]["status"] == "settled"  # the interior row is not needed to measure open-to-close over the window
    exit_day = date.fromisoformat(BASELINE[2])
    got = settled(bench={d: v for d, v in BENCH.items() if d != exit_day})
    assert dates_of(got) == BASELINE and got["horizons"][5]["status"] == "gap" and "no row" in got["horizons"][5]["reason"]


def test_deleting_any_single_benchmark_or_stock_observation_never_moves_the_intended_dates():
    for gone in SESSIONS:
        by_bench = settled(bench={d: v for d, v in BENCH.items() if d != gone})
        by_stock = settled(stock=[b for b in STOCK if b[0] != gone])
        assert dates_of(by_bench) == BASELINE, gone
        assert dates_of(by_stock) == BASELINE, gone


def test_a_known_holiday_is_skipped_but_a_weekday_that_merely_has_no_rows_is_not():
    # Monday 12 January is a recorded holiday: skipped. Suppose 14 January has no rows in either series and is NOT recorded as a holiday: it still counts as a session, with a gap, rather than vanishing.
    completed = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
    base = settled(completed=completed, horizons=(2,))
    assert base["entry_session"] == "2026-01-13" and base["horizons"][2]["exit_date"] == "2026-01-14"
    stock = [b for b in STOCK if b[0] != date(2026, 1, 14)]
    bench = {d: v for d, v in BENCH.items() if d != date(2026, 1, 14)}
    got = settled(completed=completed, horizons=(2,), stock=stock, bench=bench)
    assert got["horizons"][2]["exit_date"] == "2026-01-14" and got["horizons"][2]["status"] == "gap"


def test_a_window_the_calendar_has_not_established_yet_is_pending_not_shortened_or_shifted():
    short = timing.SessionCalendar(holidays=CAL.holidays, through=date(2026, 1, 13))  # the store has examined days only to the 13th
    got = settled(calendar=short, horizons=(2, 5))
    assert got["entry_session"] == "2026-01-08" and got["horizons"][2]["status"] == "settled" and got["horizons"][2]["exit_date"] == "2026-01-09"
    assert got["horizons"][5]["status"] == "pending" and "exit_date" not in got["horizons"][5]  # no exit date is invented for a window that is not established
    no_future = timing.SessionCalendar(holidays=CAL.holidays, through=date(2026, 1, 7))
    assert settled(calendar=no_future)["entry_session"] is None and settled(calendar=no_future)["status"] == "pending"


def test_without_an_exchange_calendar_nothing_is_settled_and_the_reason_says_so():
    got = decisions.settle_prospective(datetime(2026, 1, 7, 6, 0, tzinfo=UTC), STOCK, BENCH, (5,))
    assert got["status"] == "unverifiable" and "calendar" in got["reason"] and got["entry_session"] is None


def test_the_calendar_the_command_uses_comes_from_the_warehouses_day_records_not_its_prices(tmp_path):
    from athena.warehouse.warehouse import Warehouse

    now = datetime(2026, 1, 20, 12, 0)
    with Warehouse(tmp_path / "w.duckdb") as w:
        assert decisions.session_calendar(w) is None  # nothing examined: nothing can be settled
        w.mark_day(date(2026, 1, 12), "no_session", now)
        w.mark_day(date(2026, 1, 10), "no_session", now)  # a Saturday
        w.upsert_day(date(2026, 1, 13), {}, now)  # a loaded day (no rows needed: the day record is what counts)
        cal = decisions.session_calendar(w)
    assert cal.through == date(2026, 1, 13) and date(2026, 1, 12) in cal.holidays and cal.first_after(date(2026, 1, 9)) == date(2026, 1, 13)
    assert cal.first_after(date(2026, 1, 13)) is None  # nothing past the last examined day is assumed


def test_the_calendar_is_weekdays_less_holidays_and_stops_at_what_was_examined():
    assert CAL.first_after(date(2026, 1, 9)) == date(2026, 1, 13) and CAL.first_after(date(2026, 1, 7)) == date(2026, 1, 8)
    assert CAL.sessions_from(date(2026, 1, 8), 3) == [date(2026, 1, 8), date(2026, 1, 9), date(2026, 1, 13)]
    assert CAL.sessions_from(SESSIONS[-1], 2) is None  # one session established, two asked for
    assert CAL.first_after(SESSIONS[-1]) is None


def payload(timing_block=None):
    out = {"verdict": {"verdict": "Buy", "conviction": 40}, "taking_part": ["a", "b"], "conflict": None}
    if timing_block is not None:
        out["timing"] = timing_block
    return out


def test_a_verdict_without_a_decision_completion_record_stays_legacy_and_keeps_its_stored_association():
    history = {"ABC": [{"made_at": datetime(2026, 1, 7, 6, 0, tzinfo=UTC), "data_as_of": date(2026, 1, 1), "payload": payload()}]}
    before = copy.deepcopy(history)
    closes = lambda symbol: [(d, c) for d, _, c in STOCK]  # noqa: E731
    rows = decisions.settle_all(history, closes, {d: b[1] for d, b in BENCH.items()}, bars=lambda s: STOCK, bench_bars=BENCH, calendar=CAL)
    assert history == before  # the stored verdicts were not touched
    row = rows[0]
    assert set(row["outcomes"]) >= {5} and row["settlement"]["status"] == "unverifiable" and "no decision-completion" in row["settlement"]["reason"]
    assert row["settlement"]["policy"] == timing.ENTRY_POLICY and row["association"] == timing.LEGACY_ASSOCIATION


def test_a_verdict_with_a_timing_record_is_settled_prospectively_and_its_association_is_still_reported_apart():
    block = {"version": "timing/1", "entry_policy": timing.ENTRY_POLICY, "decision_completed_at": "2026-01-07T06:00:00+00:00", "data_as_of": "2026-01-01"}
    history = {"ABC": [{"made_at": datetime(2026, 1, 7, 5, 55, tzinfo=UTC), "data_as_of": date(2026, 1, 1), "payload": payload(block)}]}
    closes = lambda symbol: [(d, c) for d, _, c in STOCK]  # noqa: E731
    row = decisions.settle_all(history, closes, {d: b[1] for d, b in BENCH.items()}, bars=lambda s: STOCK, bench_bars=BENCH, calendar=CAL)[0]
    assert row["settlement"]["entry_session"] == "2026-01-08" and row["settlement"]["horizons"][5]["status"] == "settled"
    assert row["outcomes"][5]["stock"] != row["settlement"]["horizons"][5]["stock"]  # the two statistics are different numbers with different meanings


def test_existing_callers_without_bars_get_no_settlement_block_and_the_same_rows_as_before():
    history = {"ABC": [{"made_at": datetime(2026, 1, 7, 6, 0, tzinfo=UTC), "data_as_of": date(2026, 1, 1), "payload": payload()}]}
    rows = decisions.settle_all(history, lambda s: [(d, c) for d, _, c in STOCK], {d: b[1] for d, b in BENCH.items()})
    assert "settlement" not in rows[0] and set(rows[0]) == {"symbol", "made_at", "data_as_of", "decision", "outcomes"}


def test_the_prospective_summary_counts_only_settled_horizons_and_reports_the_rest_separately():
    def row(kind, status, excess=None):
        return {"decision": {"type": kind}, "settlement": {"horizons": {5: {"status": status, **({"excess": excess} if excess is not None else {})}}, "status": status}}

    rows = [row("BUY", "settled", 0.02), row("BUY", "settled", -0.01), row("BUY", "gap"), row("BUY", "pending"), row("SELL", "settled", -0.03),
            {"decision": {"type": "BUY"}, "settlement": {"status": "unverifiable", "horizons": {}}}]
    got = decisions.summarize_prospective(rows, buy_cost=0.005)
    assert got["policy"] == timing.ENTRY_POLICY
    assert got["by_decision"]["BUY"]["5"]["n"] == 2 and got["by_decision"]["BUY"]["5"]["mean_excess"] == pytest.approx(0.005)
    assert got["by_decision"]["BUY"]["5"]["mean_excess_after_costs"] == pytest.approx(0.0)
    assert got["by_decision"]["SELL"]["5"]["went_the_way_said"] == 1.0
    assert got["coverage"] == {"settled": 3, "gap": 1, "pending": 1, "unverifiable": 1}
    text = decisions.format_prospective(got, 6)
    assert "next_open_after_decision_v1" in text and "1 unverifiable" in text and "gap" in text


def test_same_inputs_give_the_same_settlement():
    assert settled() == settled()
