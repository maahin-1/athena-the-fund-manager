from datetime import date, datetime, timezone

import pytest

from athena.contracts import AthenaError
from athena.news.google_news import GoogleNews
from athena.news.model import company_names
from athena.news.publisher_feeds import FEEDS, PublisherFeeds
from athena.news.rss import parse_rss

ET_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Vedanta shares jump after demerger</title><link>https://et/1</link><pubDate>Fri, 09 Oct 2026 10:30:00 +0530</pubDate>
<description>&lt;p&gt;Shares of &lt;b&gt;Vedanta&lt;/b&gt; rose 4%.&lt;/p&gt;</description></item>
<item><title>Nifty ends flat</title><link>https://et/2</link><pubDate>Fri, 09 Oct 2026 15:30:00 +0530</pubDate><description>Markets</description></item>
<item><title>No date here</title><link>https://et/3</link></item>
</channel></rss>"""

GOOGLE_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Kotak Mahindra Bank 5:1 stock split: What shareholders need to know - The Economic Times</title>
<link>https://news.google.com/rss/articles/CBM1</link><pubDate>Tue, 13 Jan 2026 08:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;Kotak&lt;/a&gt;</description><source url="https://economictimes.indiatimes.com">The Economic Times</source></item>
<item><title>Split explained</title><link>https://news.google.com/rss/articles/CBM2</link><pubDate>Wed, 14 Jan 2026 08:00:00 GMT</pubDate><source url="https://x">India Today</source></item>
</channel></rss>"""


def test_rss_items_become_news_items_with_aware_times_clean_snippets_and_undated_items_dropped():
    items = parse_rss(ET_XML, "Economic Times", "publisher feed")
    assert [i.title for i in items] == ["Nifty ends flat", "Vedanta shares jump after demerger"]  # newest first
    vedanta = items[1]
    assert vedanta.published == datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc) and vedanta.published.utcoffset() is not None
    assert vedanta.snippet == "Shares of Vedanta rose 4%." and vedanta.publisher == "Economic Times" and vedanta.source == "publisher feed"


def test_a_garbled_feed_is_an_athena_error():
    with pytest.raises(AthenaError, match="not a readable feed"):
        parse_rss(b"<html>blocked", "X", "publisher feed")


def test_a_feed_that_tries_to_expand_entities_is_refused_not_parsed():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><rss><channel><item><title>&b;</title></item></channel></rss>'
    with pytest.raises(AthenaError, match="not a readable feed"):
        parse_rss(bomb, "X", "publisher feed")


def test_google_items_take_the_publisher_from_the_source_tag_and_lose_the_trailing_publisher_in_the_title():
    items = parse_rss(GOOGLE_XML, "Google News", "google news", publisher_from_source=True)
    kotak = items[1]  # newest first: India Today's 14 Jan piece, then ET's 13 Jan piece
    assert kotak.publisher == "The Economic Times" and kotak.title == "Kotak Mahindra Bank 5:1 stock split: What shareholders need to know"
    assert items[0].title == "Split explained" and items[0].publisher == "India Today" and items[0].snippet == ""


# ---- publisher feeds

def fake_get(bodies):
    calls = []

    def get(url, params=None):
        calls.append(url)
        body = bodies.get(url, ET_XML)
        if isinstance(body, Exception):
            raise body
        return body

    get.calls = calls
    return get


def test_publisher_feeds_keep_only_items_that_name_the_company_inside_the_window():
    feeds = PublisherFeeds(fake_get({}), clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))
    result = feeds("VEDL", company_names("Vedanta Limited"), date(2026, 10, 8), date(2026, 10, 10))
    assert {i.title for i in result.items} == {"Vedanta shares jump after demerger"} and len(result.items) == len(FEEDS)  # the same item from each feed
    assert not result.notes


def test_each_feed_is_fetched_once_within_ten_minutes_and_a_failed_feed_is_only_a_note():
    now = [datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)]
    first = FEEDS[0][1]
    get = fake_get({first: AthenaError("HTTP 500")})
    feeds = PublisherFeeds(get, clock=lambda: now[0])
    result = feeds("VEDL", ("Vedanta",), date(2026, 10, 8), date(2026, 10, 10))
    assert len(result.items) == len(FEEDS) - 1 and len(result.notes) == 1 and "HTTP 500" in result.notes[0]
    feeds("WIPRO", ("Wipro",), date(2026, 10, 8), date(2026, 10, 10))
    assert len(get.calls) == len(FEEDS) + 1  # only the failed feed was asked again
    now[0] = datetime(2026, 10, 10, 9, 11, tzinfo=timezone.utc)
    feeds("WIPRO", ("Wipro",), date(2026, 10, 8), date(2026, 10, 10))
    assert len(get.calls) == 2 * len(FEEDS) + 1


def test_if_every_feed_fails_that_is_an_error_not_an_empty_answer():
    feeds = PublisherFeeds(lambda url, params=None: (_ for _ in ()).throw(AthenaError("down")), clock=lambda: datetime(2026, 10, 10, tzinfo=timezone.utc))
    with pytest.raises(AthenaError, match="every publisher feed failed"):
        feeds("VEDL", ("Vedanta",), date(2026, 10, 8), date(2026, 10, 10))


# ---- google news

def test_google_news_is_off_unless_switched_on_and_says_so_without_making_a_request():
    get = fake_get({})
    result = GoogleNews(get, enabled=False)("KOTAKBANK", ("Kotak Mahindra Bank",), date(2026, 1, 8), date(2026, 1, 16))
    assert result.items == [] and "ATHENA_GOOGLE_NEWS=1" in result.notes[0] and get.calls == []


def test_google_news_asks_for_the_company_in_quotes_with_the_date_window_and_the_extra_words():
    seen = {}

    def get(url, params=None):
        seen.update(params or {})
        return GOOGLE_XML

    result = GoogleNews(get, enabled=True, sleep=lambda s: None)("KOTAKBANK", ("Kotak Mahindra Bank", "Kotak Mahindra Bank Limited"), date(2026, 1, 8), date(2026, 1, 16), extra="stock split")
    assert seen["q"] == '"Kotak Mahindra Bank" stock split after:2026-01-08 before:2026-01-16'
    assert seen["hl"] == "en-IN" and seen["gl"] == "IN" and seen["ceid"] == "IN:en"
    assert len(result.items) == 2 and result.items[0].publisher == "India Today"  # newest first


def test_google_news_waits_between_requests_and_a_failure_is_an_athena_error():
    waits, clock = [], [100.0]
    news = GoogleNews(lambda url, params=None: GOOGLE_XML, enabled=True, sleep=waits.append, now=lambda: clock[0])
    news("A", ("A",), date(2026, 1, 1), date(2026, 1, 2))
    clock[0] = 100.4
    news("B", ("B",), date(2026, 1, 1), date(2026, 1, 2))
    assert waits == [pytest.approx(0.6)]

    def down(url, params=None):
        raise AthenaError("HTTP 503")

    with pytest.raises(AthenaError, match="Google News unavailable"):
        GoogleNews(down, enabled=True, sleep=lambda s: None)("A", ("A",), date(2026, 1, 1), date(2026, 1, 2))
