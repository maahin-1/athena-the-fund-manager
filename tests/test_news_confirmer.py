from datetime import date, datetime, timezone

from athena.news.confirmer import EventConfirmer
from athena.news.hub import NewsHub
from athena.news.model import NewsItem, SourceResult

KOTAK_DAY = date(2026, 1, 14)


def headline(day, title, publisher="India Today"):
    return NewsItem(title, publisher, datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc), "https://x", "google news")


def hub_with(items, notes=(), calls=None):
    def source(symbol, names, since, until, extra=""):
        if calls is not None:
            calls.append((symbol, since, until, extra))
        return SourceResult([i for i in items if since <= i.published.date() <= until], tuple(notes))

    return NewsHub([("fake", source)])


def test_each_found_event_is_looked_up_in_its_own_window_with_words_for_its_kind_and_described():
    calls = []
    hub = hub_with([headline(KOTAK_DAY, "Kotak stock split: did shares really crash 80% today?")], calls=calls)
    confirm = EventConfirmer(hub, lambda symbol: [(KOTAK_DAY, "split", "split (x0.2)")], today=lambda: date(2026, 10, 10))
    notes = confirm("KOTAKBANK", "Kotak Mahindra Bank Limited")
    assert calls == [("KOTAKBANK", date(2025, 12, 31), date(2026, 1, 21), "stock split")]
    assert len(notes) == 1 and "split (x0.2) on 2026-01-14: confirmed by 1 headline" in notes[0]


def test_only_the_five_most_recent_events_are_looked_up_and_the_window_never_runs_past_today():
    events = [(date(2026, 1, d), "other", f"row {d}") for d in range(1, 9)] + [(date(2026, 10, 8), "buyback", "Buy Back")]
    calls = []
    notes = EventConfirmer(hub_with([], calls=calls), lambda symbol: events, today=lambda: date(2026, 10, 10))("X", "X Limited")
    assert len(calls) == 5 and len(notes) == 5 and calls[-1][2] == date(2026, 10, 10)  # oldest of the five first; capped at today


def test_a_source_note_is_shown_once_even_when_several_events_ask_for_the_same_source():
    events = [(date(2026, 1, 14), "split", "split"), (date(2026, 4, 30), "demerger", "demerger")]
    notes = EventConfirmer(hub_with([], notes=("Google News is off.",)), lambda symbol: events, today=lambda: date(2026, 10, 10))("X", "X Limited")
    assert sum("Google News is off." == n for n in notes) == 1 and len(notes) == 3


def test_no_events_means_no_lookups_and_no_notes_and_a_failing_hub_never_raises():
    calls = []
    assert EventConfirmer(hub_with([], calls=calls), lambda symbol: [])("X", "X Limited") == () and calls == []

    class Exploding:
        def fetch(self, *args, **kwargs):
            raise RuntimeError("boom")

    notes = EventConfirmer(Exploding(), lambda symbol: [(KOTAK_DAY, "split", "split")], today=lambda: date(2026, 10, 10))("X", "X Limited")
    assert len(notes) == 1 and "could not be checked against the news" in notes[0]


def test_the_same_events_are_not_looked_up_twice_for_one_symbol():
    calls = []
    confirm = EventConfirmer(hub_with([], calls=calls), lambda symbol: [(KOTAK_DAY, "split", "split")], today=lambda: date(2026, 10, 10))
    confirm("X", "X Limited")
    confirm("X", "X Limited")
    assert len(calls) == 1
