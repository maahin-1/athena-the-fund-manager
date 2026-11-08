from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from athena.evidence import attention as at
from athena.evidence.attention import attention, find_article, pageview_view, vix_view, wikipedia_view
from athena.evidence.sources import TRIED_AND_OFF
from athena.sentiment import page
from athena.sentiment.reddit import RedditSearch

AS_OF = date(2026, 11, 6)


def days(n: int, end: date = AS_OF) -> list[date]:
    return [end - timedelta(days=n - 1 - i) for i in range(n)]


def test_the_vix_level_is_ranked_against_the_last_year_and_all_days_held_and_its_20_day_change_is_worked_out() -> None:
    ds = days(400)
    close = {d: 10.0 + i * 0.05 for i, d in enumerate(ds)}  # rising every day, 10 to 29.95
    got = vix_view(close, AS_OF)
    assert got["status"] == "ok" and got["day"] == AS_OF.isoformat() and got["level"] == 29.95
    assert got["percentile_1y"] == 100.0 and got["percentile_all"] == 100.0 and got["change_20d_points"] == 1.0 and got["stale_days"] == 0
    falling = {d: 30.0 - i * 0.05 for i, d in enumerate(ds)}
    low = vix_view(falling, AS_OF)
    assert low["percentile_1y"] < 1.0 and low["change_20d_points"] == -1.0


def test_the_vix_uses_only_days_up_to_the_day_asked_and_says_when_it_has_too_few() -> None:
    ds = days(100)
    close = {d: 15.0 for d in ds}
    close[AS_OF] = 99.0  # a spike on the as-of day itself
    earlier = vix_view(close, AS_OF - timedelta(days=1))
    assert earlier["level"] == 15.0 and earlier["day"] == (AS_OF - timedelta(days=1)).isoformat()
    short = vix_view({d: 15.0 for d in days(10)}, AS_OF)
    assert short["status"] == "off" and "at least 30" in short["reason"]


def get_from(table: dict[str, object]):  # noqa: ANN201
    def get(url: str):  # noqa: ANN202
        for key, value in table.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise RuntimeError("the server answered 404")

    return get


SEARCH = {"pages": [{"key": "Reliance_Industries", "title": "Reliance Industries", "matched_title": "Reliance Industries Limited", "description": "Indian multinational conglomerate"}]}


def test_an_article_is_taken_only_when_its_title_holds_every_word_of_the_name() -> None:
    assert find_article("Reliance Industries Limited", get_from({"search/title": SEARCH}))["article"] == "Reliance_Industries"
    assert find_article("Reliance Power Limited", get_from({"search/title": SEARCH})) is None  # `power` is not in the title
    assert find_article("  ", get_from({"search/title": SEARCH})) is None
    other = {"pages": [{"key": "Infosys_BPM", "title": "Infosys BPM", "description": ""}, {"key": "Infosys", "title": "Infosys", "description": "Indian IT company"}]}
    assert find_article("Infosys Limited", get_from({"search/title": other}))["article"] == "Infosys"  # `Infosys BPM` adds a word, so it is another entity
    assert find_article("Infosys", get_from({"search/title": {"pages": [other["pages"][0]]}})) is None


def views(n: int, base: int, last_week: int | None = None, spike: tuple[int, int] | None = None, end: date = AS_OF) -> list[dict]:
    ds = days(n, end)
    out = []
    for i, d in enumerate(ds):
        v = base
        if last_week is not None and i >= n - 7:
            v = last_week
        if spike and i == spike[0]:
            v = spike[1]
        out.append({"timestamp": d.strftime("%Y%m%d") + "00", "views": v})
    return out


def test_the_last_weeks_page_views_are_set_against_the_median_before_and_spikes_are_listed() -> None:
    rows = views(120, 1000, last_week=1500, spike=(100, 5000))
    got = pageview_view(rows, AS_OF)
    assert got["status"] == "ok" and got["last_7_days_daily_average"] == 1500.0 and got["median_daily_views_before"] == 1000.0 and got["ratio"] == 1.5
    assert got["peak_views"] == 5000 and got["peak_day"] == days(120)[100].isoformat() and got["days_above_3x_median"] == [days(120)[100].isoformat()]


def test_page_views_after_the_day_asked_are_not_used_and_a_short_series_is_off() -> None:
    rows = views(120, 1000) + [{"timestamp": (AS_OF + timedelta(days=1)).strftime("%Y%m%d") + "00", "views": 10**6}]
    got = pageview_view(rows, AS_OF)
    assert got["to"] == AS_OF.isoformat() and got["peak_views"] == 1000  # a million views tomorrow is not known today
    assert pageview_view(views(20, 1000), AS_OF)["status"] == "off"
    assert pageview_view([{"timestamp": "bad", "views": 1}, {"views": "x"}], AS_OF)["status"] == "off"


def test_wikipedia_view_gives_the_article_and_a_failure_is_a_state_with_a_reason() -> None:
    get = get_from({"search/title": SEARCH, "pageviews": {"items": views(120, 1000)}})
    ok = wikipedia_view("Reliance Industries Limited", AS_OF, get, sleep=lambda s: None)
    assert ok["status"] == "ok" and ok["article"] == "Reliance Industries" and ok["link"] == "https://en.wikipedia.org/wiki/Reliance_Industries" and ok["description"] == "Indian multinational conglomerate"
    failed = wikipedia_view("Reliance Industries Limited", AS_OF, get_from({"search/title": RuntimeError("the server answered 503")}), sleep=lambda s: None)
    assert failed["status"] == "off" and "503" in failed["reason"]
    none = wikipedia_view("Nowhere Limited", AS_OF, get_from({"search/title": {"pages": []}}), sleep=lambda s: None)
    assert none["status"] == "off" and "no Wikipedia article" in none["reason"]


def test_the_document_carries_the_sources_tried_and_off_and_a_second_call_within_six_hours_makes_no_request(tmp_path) -> None:  # noqa: ANN001
    calls = []
    base = get_from({"search/title": SEARCH, "pageviews": {"items": views(120, 1000)}})

    def get(url: str):  # noqa: ANN202
        calls.append(url)
        return base(url)

    close = {d: 15.0 for d in days(100)}
    now = datetime(2026, 11, 6, 10, 0)
    cache = tmp_path / "cache.json"
    first = attention("RELIANCE", "Reliance Industries Limited", AS_OF, close, get, cache, now, sleep=lambda s: None)
    n = len(calls)
    second = attention("RELIANCE", "Reliance Industries Limited", AS_OF, close, get, cache, now + timedelta(hours=1), sleep=lambda s: None)
    assert n == 2 and len(calls) == n and second["wikipedia"] == first["wikipedia"]
    later = attention("RELIANCE", "Reliance Industries Limited", AS_OF, close, get, cache, now + timedelta(hours=7), sleep=lambda s: None)
    assert len(calls) == 2 * n and later["schema"] == "athena.attention/1"
    assert [o["id"] for o in first["off"]] == [s.id for s in TRIED_AND_OFF] and "gdelt" in [o["id"] for o in first["off"]] and "not sentiment" in first["caveat"]


def test_a_failed_read_is_not_cached(tmp_path) -> None:  # noqa: ANN001
    cache = tmp_path / "cache.json"
    attention("X", "Nowhere Limited", AS_OF, {d: 1.0 for d in days(40)}, get_from({"search/title": {"pages": []}}), cache, datetime(2026, 11, 6), sleep=lambda s: None)
    assert not cache.exists()  # an article that was not found is asked for again next time


def test_the_sentiment_page_carries_the_attention_document_and_a_failure_of_it_is_a_note_not_the_end_of_the_page() -> None:
    class Hub:
        def fetch(self, symbol, company, since, until):  # noqa: ANN001, ANN201
            return SimpleNamespace(items=[], notes=())

    doc = page.build_page("INFY", "Infosys Limited", AS_OF, Hub(), RedditSearch(None), None, None, lambda: {"schema": at.SCHEMA, "vix": {"status": "off", "reason": "x"}})
    assert doc["attention"]["schema"] == "athena.attention/1"

    def broken() -> dict:
        raise RuntimeError("no network")

    failed = page.build_page("INFY", "Infosys Limited", AS_OF, Hub(), RedditSearch(None), None, None, broken)
    assert failed["attention"] is None and "attention unavailable: RuntimeError" in failed["news"]["notes"]
    assert page.build_page("INFY", "Infosys Limited", AS_OF, Hub(), RedditSearch(None))["attention"] is None
