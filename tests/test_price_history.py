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
