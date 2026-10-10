from datetime import date, datetime, timezone

from athena.contracts import AthenaError
from athena.news.hub import NewsHub, default_sources
from athena.news.model import NewsItem, SourceResult

SINCE, UNTIL = date(2026, 4, 20), date(2026, 5, 1)


def item(title, day, source="x", publisher="P"):
    return NewsItem(title, publisher, datetime(2026, 4, day, 12, 0, tzinfo=timezone.utc), "https://x", source)


def source(items=(), notes=(), error=None):
    def fetch(symbol, names, since, until, extra=""):
        if error:
            raise error
        return SourceResult(list(items), tuple(notes)) if notes else list(items)

    return fetch


def test_items_from_every_source_come_back_newest_first_and_either_return_style_works():
    hub = NewsHub([("nse", source([item("Record date fixed", 20)])), ("feeds", source([item("Vedanta demerger", 29)], notes=("Mint feed unavailable: HTTP 500",)))])
    result = hub.fetch("VEDL", "Vedanta Limited", SINCE, UNTIL)
    assert [i.title for i in result.items] == ["Vedanta demerger", "Record date fixed"]
    assert result.notes == ("Mint feed unavailable: HTTP 500",)


def test_a_source_that_fails_is_a_note_naming_it_and_the_others_still_answer():
    hub = NewsHub([("nse", source(error=AthenaError("NSE announcements unavailable for VEDL: HTTP 403"))), ("feeds", source([item("Vedanta demerger", 29)]))])
    result = hub.fetch("VEDL", "Vedanta Limited", SINCE, UNTIL)
    assert len(result.items) == 1 and result.notes == ("NSE announcements unavailable for VEDL: HTTP 403",)


def test_the_same_headline_from_two_sources_is_kept_once_ignoring_case_and_punctuation():
    hub = NewsHub([("a", source([item("Vedanta: Demerger done!", 29, "nse")])), ("b", source([item("vedanta demerger done", 29, "google news")]))])
    assert len(hub.fetch("VEDL", "Vedanta Limited", SINCE, UNTIL).items) == 1


def test_the_hub_passes_the_company_names_the_window_and_the_extra_words_to_every_source():
    seen = []

    def spy(symbol, names, since, until, extra=""):
        seen.append((symbol, names, since, until, extra))
        return []

    NewsHub([("spy", spy)]).fetch("VEDL", "Vedanta Limited", SINCE, UNTIL, extra="demerger")
    assert seen == [("VEDL", ("Vedanta", "Vedanta Limited"), SINCE, UNTIL, "demerger")]


def test_default_sources_adapt_the_three_clients_to_one_signature():
    calls = []
    sources = dict(default_sources(
        nse=lambda symbol, since, until: calls.append(("nse", symbol, since, until)) or [],
        feeds=lambda symbol, names, since, until: calls.append(("feeds", names)) or SourceResult([]),
        google=lambda symbol, names, since, until, extra="": calls.append(("google", extra)) or SourceResult([]),
    ))
    for fetch in sources.values():
        fetch("VEDL", ("Vedanta",), SINCE, UNTIL, "demerger")
    assert list(sources) == ["NSE announcements", "publisher feeds", "Google News"]
    assert calls == [("nse", "VEDL", SINCE, UNTIL), ("feeds", ("Vedanta",)), ("google", "demerger")]
