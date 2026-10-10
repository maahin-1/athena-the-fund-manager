from bar_factory import make_bars

from athena.contracts import AthenaError
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.history import CheckedHistory
from athena.trading_calendar import ist_date

BARS = make_bars([100.0] * 30 + [20.0] * 30, symbol="KOTAKBANK")
EX_DATE = ist_date(BARS[30].timestamp)


def test_fetched_bars_come_back_adjusted_with_a_note_saying_so():
    history = CheckedHistory(lambda symbol: list(BARS), lambda symbol: [PriceEvent(EX_DATE, 5.0)])
    assert [b.close for b in history("KOTAKBANK")] == [20.0] * 60
    assert "adjusted for 1 recorded" in history.notes("KOTAKBANK")[0]
    assert history.notes("OTHER") == ()


def test_a_failed_event_lookup_does_not_fail_the_request_and_still_flags_the_break():
    def broken(symbol):
        raise AthenaError("corporate actions unavailable for KOTAKBANK: blocked")

    history = CheckedHistory(lambda symbol: list(BARS), broken)
    assert [b.close for b in history("KOTAKBANK")] == [100.0] * 30 + [20.0] * 30
    notes = history.notes("KOTAKBANK")
    assert "corporate actions unavailable" in notes[0] and any("Unexplained" in note for note in notes)


def test_no_events_and_no_breaks_means_no_notes_and_the_bars_are_returned_unchanged():
    flat = make_bars([100.0] * 40)
    history = CheckedHistory(lambda symbol: list(flat), lambda symbol: [])
    assert history("X") == flat and history.notes("X") == ()


# ---- with NSE's corporate actions as a second source

from athena.price_integrity.actions import CorporateAction  # noqa: E402

DEMERGER_BARS = make_bars([100.0] * 30 + [37.0] * 30, symbol="VEDL")
DEMERGER_DAY = ist_date(DEMERGER_BARS[30].timestamp)


def test_nse_demerger_row_adjusts_what_yahoo_cannot_know():
    history = CheckedHistory(lambda s: list(DEMERGER_BARS), lambda s: [], lambda s: [CorporateAction(DEMERGER_DAY, "demerger", None, "Demerger")])
    assert [round(b.close, 4) for b in history("VEDL")] == [37.0] * 60
    assert "demerger" in history.notes("VEDL")[0]


def test_nse_failing_leaves_yahoos_events_working_and_says_so():
    def blocked(symbol):
        raise AthenaError("NSE corporate actions unavailable for KOTAKBANK: HTTP 403")

    history = CheckedHistory(lambda s: list(BARS), lambda s: [PriceEvent(EX_DATE, 5.0)], blocked)
    assert [b.close for b in history("KOTAKBANK")] == [20.0] * 60
    notes = history.notes("KOTAKBANK")
    assert "NSE corporate actions unavailable" in notes[0] and any("adjusted for 1 recorded" in n for n in notes)


def test_yahoo_failing_leaves_nses_split_text_working():
    def broken(symbol):
        raise AthenaError("corporate actions unavailable for KOTAKBANK: blocked")

    row = CorporateAction(EX_DATE, "split", 5.0, "Face Value Split From Rs 5 To Re 1")
    history = CheckedHistory(lambda s: list(BARS), broken, lambda s: [row])
    assert [b.close for b in history("KOTAKBANK")] == [20.0] * 60


def test_a_ratio_conflict_between_the_sources_adjusts_nothing_and_flags_it():
    row = CorporateAction(EX_DATE, "split", 10.0, "Face Value Split From Rs 10 To Re 1")
    history = CheckedHistory(lambda s: list(BARS), lambda s: [PriceEvent(EX_DATE, 5.0)], lambda s: [row])
    assert [b.close for b in history("KOTAKBANK")] == [100.0] * 30 + [20.0] * 30
    notes = history.notes("KOTAKBANK")
    assert "disagree" in notes[0] and any("Unexplained" in n for n in notes)


def test_the_events_found_by_the_last_fetch_are_listed_oldest_first_with_a_label():
    from athena.price_integrity.actions import CorporateAction

    bars = make_bars([100.0] * 20 + [20.0] * 20 + [8.0] * 20, symbol="Z")
    split_day, break_day = ist_date(bars[20].timestamp), ist_date(bars[40].timestamp)
    buyback = CorporateAction(ist_date(bars[10].timestamp), "buyback", None, "Buy Back")
    history = CheckedHistory(lambda s: list(bars), lambda s: [PriceEvent(split_day, 5.0)], lambda s: [buyback])
    assert history.events_found("Z") == ()  # nothing fetched yet
    history("Z")
    found = history.events_found("Z")
    assert [(day, kind) for day, kind, _ in found] == [(buyback.day, "buyback"), (split_day, "split"), (break_day, "unexplained")]
    assert found[1][2] == "split (x0.2)" and found[2][2].startswith("unexplained price break") and found[0][2] == "Buy Back"
