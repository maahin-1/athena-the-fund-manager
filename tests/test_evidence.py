from datetime import datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.evidence import collect as col
from athena.evidence.sources import BY_ID, FEED_SOURCES, IST, FeedSource, tier_of
from athena.evidence.store import EvidenceStore, headline_words, same_story
from athena.news.model import NewsItem
from athena.news.rss import parse_rss, parse_when

NOW = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)


def item(title, hours_ago=1.0, link=None, publisher="Mint"):
    return NewsItem(title, publisher, NOW - timedelta(hours=hours_ago), link or f"https://x/{title[:20]}", "feed", "excerpt")


# --- dates ---
def test_every_date_style_the_indian_feeds_use_is_read_and_a_missing_zone_is_the_feeds_own():
    utc = timezone.utc
    assert parse_when("Thu, 29 Oct 2026 12:05:00 +0530") == datetime(2026, 10, 29, 6, 35, tzinfo=utc)
    assert parse_when("2026-10-29T12:47:30+05:30") == datetime(2026, 10, 29, 7, 17, 30, tzinfo=utc)
    assert parse_when("29 Oct 2026 12:00:02 +0530") == datetime(2026, 10, 29, 6, 30, 2, tzinfo=utc)
    assert parse_when("28 Oct, 2026 +0530") == datetime(2026, 10, 27, 18, 30, tzinfo=utc)
    assert parse_when("Thu, 29 Oct 2026 12:05:00", IST) == datetime(2026, 10, 29, 6, 35, tzinfo=utc)  # the RBI prints India time with no zone
    assert parse_when("Thu, 29 Oct 2026 12:05:00") == datetime(2026, 10, 29, 12, 5, tzinfo=utc)  # and read as UTC without that knowledge it would be 5.5 hours early
    for bad in (None, "", "   ", "yesterday", "32 Oct 2026"):
        assert parse_when(bad) is None


def test_an_item_with_no_readable_date_is_still_skipped_and_the_feeds_zone_is_used():
    body = b"<rss><channel><item><title>A</title><link>l</link></item><item><title>B</title><pubDate>Thu, 29 Oct 2026 12:05:00</pubDate></item></channel></rss>"
    got = parse_rss(body, "RBI", "rbi", default_tz=IST)
    assert [i.title for i in got] == ["B"] and got[0].published == datetime(2026, 10, 29, 6, 35, tzinfo=timezone.utc)


# --- stories ---
def test_the_same_agency_story_under_two_headlines_is_one_story_and_a_different_one_is_not():
    a = headline_words("Reliance Q2 profit rises 12% on retail strength, beats Street estimates")
    b = headline_words("Reliance Q2 profit rises 12% on retail strength, beats estimates")
    c = headline_words("Reliance announces rights issue to raise capital")
    assert same_story(a, b) and not same_story(a, c)
    assert not same_story(headline_words("Up"), headline_words("Down"))


# --- the store ---
def test_items_are_stored_once_first_seen_is_kept_and_copies_share_a_story(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    first = [item("Reliance Q2 profit rises 12% on retail strength, beats Street estimates", 2, "https://a/1", "Mint")]
    assert store.add_items("mint_markets", first, NOW) == 1
    assert store.add_items("mint_markets", first, NOW + timedelta(hours=3)) == 0  # seen again later: nothing new, and first_seen does not move
    copy = [item("Reliance Q2 profit rises 12% on retail strength, beats estimates", 1, "https://b/2", "Economic Times")]
    assert store.add_items("et_markets", copy, NOW + timedelta(hours=3)) == 1
    rows = store.items_between(NOW - timedelta(days=1), NOW + timedelta(days=1))
    assert len(rows) == 2 and rows[0].story_id == rows[1].story_id
    mint = [r for r in rows if r.source_id == "mint_markets"][0]
    assert mint.first_seen_at == NOW and mint.published_at.tzinfo is not None


def test_the_same_link_from_two_sources_is_two_items_and_the_window_and_source_filters_work(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    store.add_items("a", [item("Tata Motors sales jump in October on SUV demand", 5, "https://same")], NOW)
    store.add_items("b", [item("Tata Motors sales jump in October on SUV demand", 4, "https://same")], NOW)
    store.add_items("a", [item("Old item about something else entirely here", 24 * 40, "https://old")], NOW)
    assert len(store.items_between(NOW - timedelta(days=30), NOW)) == 2
    assert len(store.items_between(NOW - timedelta(days=30), NOW, ["a"])) == 1
    assert store.items_between(NOW - timedelta(days=30), NOW, []) == []


def test_health_counts_failures_in_a_row_and_a_good_pass_clears_them(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    store.record_health("x", NOW, False, "failed", "HTTP 503")
    store.record_health("x", NOW, False, "failed", "HTTP 503")
    assert store.health()["x"]["fail_streak"] == 2 and store.health()["x"]["last_ok"] is None
    store.record_health("x", NOW, True, "ok", "", 10, NOW)
    row = store.health()["x"]
    assert row["fail_streak"] == 0 and row["last_ok"] is not None and row["status"] == "ok"


# --- collecting ---
RSS = "<rss><channel>{}</channel></rss>"
ONE = "<item><title>{t}</title><link>https://x/{n}</link><pubDate>{d}</pubDate></item>"


def feed(*pairs):
    return RSS.format("".join(ONE.format(t=t, n=n, d=d) for n, (t, d) in enumerate(pairs))).encode()


def test_a_pass_marks_each_feed_ok_failed_empty_or_stale_and_goes_on_after_a_failure(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    specs = [FeedSource(i, i.upper(), "press", "B", f"https://f/{i}") for i in ("good", "down", "blank", "frozen")] + [FeedSource("off", "Off", "press", "B", "https://f/off", off_reason="no")]
    fresh = "Thu, 29 Oct 2026 07:00:00 +0000"
    bodies = {
        "https://f/good": feed(("Infosys wins large deal from European bank", fresh)),
        "https://f/blank": RSS.format("").encode(),
        "https://f/frozen": feed(("Old story about a company long ago", "Tue, 23 Apr 2024 07:00:00 +0000")),
    }

    def get(url, params=None):
        if url not in bodies:
            raise AthenaError("HTTP 503")
        return bodies[url]

    slept = []
    out = col.collect(store, specs, get, NOW, slept.append, log=lambda *_: None)
    assert out == {"good": ("ok", 1), "down": ("failed", 0), "blank": ("empty", 0), "frozen": ("stale", 1)}
    assert len(slept) == 3  # a second between requests, none before the first
    health = store.health()
    assert health["good"]["status"] == "ok" and health["down"]["status"] == "failed" and health["frozen"]["status"] == "stale" and health["off"]["status"] == "off"
    assert "2024-04-23" in health["frozen"]["error"] and health["off"]["error"] == "no"


def test_an_item_dated_in_the_future_is_not_kept(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    spec = FeedSource("f", "F", "press", "B", "https://f/f")
    body = feed(("Real item with a date today", "Thu, 29 Oct 2026 07:00:00 +0000"), ("Item claiming tomorrow", "Fri, 30 Oct 2026 07:00:00 +0000"))
    assert col.collect_one(spec, store, lambda url, params=None: body, NOW) == ("ok", 1)


def test_a_surprise_in_one_feed_is_recorded_and_does_not_end_the_pass(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    spec = FeedSource("f", "F", "press", "B", "https://f/f")

    def boom(url, params=None):
        raise ValueError("odd")

    assert col.collect_one(spec, store, boom, NOW) == ("failed", 0)
    assert "ValueError" in store.health()["f"]["error"]


# --- the registry ---
def test_the_registry_has_unique_ids_valid_tiers_and_every_off_source_says_why():
    assert len(BY_ID) == len(FEED_SOURCES)
    assert all(s.tier in "ABC" for s in FEED_SOURCES)
    assert all(s.off_reason for s in FEED_SOURCES if not s.active)
    assert {"sebi", "rbi_press"} <= {s.id for s in FEED_SOURCES if s.scope == "market"}
    assert BY_ID["rbi_press"].tz == IST
    assert {s.id for s in FEED_SOURCES if not s.active} >= {"moneycontrol", "pib", "financial_express"}


def test_the_older_news_path_gets_a_tier():
    assert tier_of("NSE", "nse") == "A" and tier_of("Mint") == "B" and tier_of("Some blog") == "C"


def test_a_feed_with_an_unescaped_ampersand_is_repaired_and_one_that_is_not_xml_still_fails():
    body = b"<rss><channel><item><title>Bombay Coated & Special Steels IPO opens</title><link>l</link><pubDate>Thu, 29 Oct 2026 12:05:00 +0000</pubDate></item></channel></rss>"
    assert [i.title for i in parse_rss(body, "BS", "bs")] == ["Bombay Coated & Special Steels IPO opens"]
    with pytest.raises(AthenaError):
        parse_rss(b"<html><body>not a feed</body", "BS", "bs")
