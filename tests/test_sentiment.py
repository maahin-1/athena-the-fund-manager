import io
import json
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.news.hub import NewsResult
from athena.news.model import NewsItem
from athena.sentiment import page
from athena.sentiment.reddit import OFF_NOTE, RedditSearch, credentials, names_it

TODAY = date(2026, 10, 23)
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def listing(*posts):
    return {"data": {"children": [{"data": p} for p in posts]}}


def reddit_post(title, text="", sub="IndianStockMarket", created=None, score=10, comments=3, permalink=None):
    return {"title": title, "selftext": text, "subreddit": sub, "created_utc": (created or NOW).timestamp(), "score": score, "num_comments": comments, "permalink": permalink or f"/r/{sub}/comments/{abs(hash(title)) % 10**6}/x/", "author": "someone"}


class Net:
    def __init__(self, response):
        self.response, self.posts, self.gets, self.sleeps = response, [], [], []

    def post(self, url, data, auth, headers):
        self.posts.append((url, dict(data), auth, dict(headers)))
        return {"access_token": "tok", "expires_in": 3600}

    def get(self, url, params, headers):
        self.gets.append((url, dict(params), dict(headers)))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


CREDS = ("id", "secret", "athena-owner-research by u/test")


def search(response, creds=CREDS):
    net = Net(response)
    return RedditSearch(creds, net.post, net.get, net.sleeps.append), net


def test_reddit_is_off_without_the_three_settings_and_says_how_to_turn_it_on(tmp_path):
    env = tmp_path / "settings"
    env.write_text("REDDIT_CLIENT_ID=abc\nREDDIT_CLIENT_SECRET=\n", encoding="utf-8")
    assert credentials(env, {}) is None
    env.write_text("REDDIT_CLIENT_ID=a\nREDDIT_CLIENT_SECRET=b\nREDDIT_USER_AGENT=c by u/d\n", encoding="utf-8")
    assert credentials(env, {}) == ("a", "b", "c by u/d")
    off, net = search(listing(), creds=None)
    assert not off.enabled
    with pytest.raises(AthenaError, match="Reddit is off"):
        off.search("TCS", "Tata Consultancy Services Limited")
    assert net.posts == [] and net.gets == [] and "REDDIT_CLIENT_ID" in OFF_NOTE


def test_a_post_counts_only_when_it_names_the_company_and_no_author_is_kept():
    posts = listing(
        reddit_post("TCS results: margins hold up", "Revenue was flat."),
        reddit_post("Which IT stock to buy?", "Thinking about Tata Consultancy Services and Infosys", score=50),
        reddit_post("Best sector for next year", "nothing about it"),
        reddit_post("tcs is a great place to work", "lowercase tcs is not the symbol written in capitals"),
        reddit_post("TCS results: margins hold up", "duplicate permalink", permalink="/dup"),
        reddit_post("TCS again", permalink="/dup"),
    )
    s, net = search(posts)
    got = s.search("TCS", "Tata Consultancy Services Limited")
    assert [p.title for p in got] == ["TCS results: margins hold up", "Which IT stock to buy?", "TCS again"] or len(got) == 3
    assert {p.title for p in got} >= {"TCS results: margins hold up", "Which IT stock to buy?"}
    assert not any("great place to work" in p.title or "Best sector" in p.title for p in got)
    assert all(p.link.startswith("https://www.reddit.com/") for p in got)
    assert not any("author" in repr(p) or "someone" in repr(p) for p in got)
    url, params, headers = net.gets[0]
    assert "oauth.reddit.com" in url and params["restrict_sr"] == "1" and params["sort"] == "new" and '"TCS"' in params["q"] and '"Tata Consultancy Services"' in params["q"]
    assert headers["Authorization"] == "Bearer tok" and headers["User-Agent"] == CREDS[2]
    assert net.posts[0][1] == {"grant_type": "client_credentials"} and net.posts[0][2] == ("id", "secret")
    assert net.sleeps == [1.1]  # at most a request a second


def test_names_are_matched_as_whole_words_and_a_short_name_is_not_a_match():
    names = ("Tata Consultancy Services", "Tata Consultancy Services Limited")
    assert names_it("TCS news", "TCS", names)
    assert not names_it("ATCS and TCSX", "TCS", names)
    assert names_it("the tata consultancy services deal", "TCS", names)
    assert not names_it("tcs only lowercase", "TCS", ())
    assert names_it("M&M launches", "M&M", ("Mahindra & Mahindra",))
    assert not names_it("an M&MS candy", "M&M", ())


def test_reddit_failures_are_told_not_raised_past_the_page():
    s, _ = search(AthenaError("Reddit asked us to slow down (HTTP 429); try again later"))
    with pytest.raises(AthenaError, match="slow down"):
        s.search("TCS", "Tata Consultancy Services")
    broken = RedditSearch(CREDS, lambda *a: {}, lambda *a: {}, lambda s: None)
    with pytest.raises(AthenaError, match="no token"):
        broken.search("TCS", "Tata Consultancy Services")


class Hub:
    def __init__(self, items, notes=()):
        self.items, self.notes = items, tuple(notes)

    def fetch(self, symbol, company, since, until, extra=""):
        return NewsResult(tuple(self.items), self.notes)


def item(title, source="nse", days_ago=1, publisher="NSE", link="https://example.test/a"):
    return NewsItem(title, publisher, NOW - timedelta(days=days_ago), link, source)


def test_the_page_marks_routine_filings_and_press_that_does_not_name_the_company_and_leaves_out_what_is_old():
    items = [
        item("Outcome of Board Meeting: results for the quarter"),
        item("Trading window closure", days_ago=2),
        item("Tata Consultancy Services wins large deal", source="publisher", publisher="Mint", link="javascript:alert(1)"),
        item("Sector round-up: IT stocks", source="google", publisher="Other"),
        item("An old filing", days_ago=60),
    ]
    doc = page.build_page("TCS", "Tata Consultancy Services Limited", TODAY, Hub(items, ["a feed was down"]), RedditSearch(None))
    rows = {r["title"]: r for r in doc["news"]["items"]}
    assert "An old filing" not in rows
    assert rows["Trading window closure"]["routine"] and not rows["Trading window closure"]["used"]
    assert rows["Outcome of Board Meeting: results for the quarter"]["used"]
    assert rows["Tata Consultancy Services wins large deal"]["used"] and rows["Tata Consultancy Services wins large deal"]["link"] == ""  # a link that is not http(s) is dropped
    assert not rows["Sector round-up: IT stocks"]["used"]
    assert doc["news"]["counts"] == {"all": 4, "used": 2, "routine": 1, "press": 2}
    assert doc["news"]["notes"] == ["a feed was down"]
    assert doc["social"]["enabled"] is False and "Reddit is off" in doc["social"]["notes"][0]
    assert doc["tone"] is None and "has not been tested" in doc["caveat"]
    assert doc["news"]["items"][0]["date"] >= doc["news"]["items"][-1]["date"]  # newest first


def test_the_page_carries_reddit_posts_and_a_reading_only_when_asked():
    s, _ = search(listing(reddit_post("TCS Q2 thread", "discussion", sub="IndiaInvestments", score=80), reddit_post("TCS dividend", sub="IndianStockMarket")))
    calls = []

    def tone(packet):
        calls.append(packet)
        return {"signal": "neutral", "confidence": 20, "reasoning": "Thin.", "data_coverage": "partial", "missing": []}

    doc = page.build_page("TCS", "Tata Consultancy Services Limited", TODAY, Hub([item("Outcome of Board Meeting")]), s, tone)
    assert doc["social"]["enabled"] and doc["social"]["by_subreddit"] == {"IndiaInvestments": 1, "IndianStockMarket": 1}
    assert doc["social"]["items"][0]["subreddit"] in ("IndiaInvestments", "IndianStockMarket") and "author" not in doc["social"]["items"][0]
    assert doc["tone"]["signal"] == "neutral" and len(calls) == 1
    assert "headlines" in calls[0] and "social" not in json.dumps(calls[0]).lower()  # the posts never reach the analyst
    failing, _ = search(AthenaError("Reddit refused the search (HTTP 403)"))
    doc = page.build_page("TCS", "Tata Consultancy Services Limited", TODAY, Hub([]), failing)
    assert doc["social"]["items"] == [] and doc["social"]["notes"] == ["Reddit refused the search (HTTP 403)"]


def test_the_command_refuses_a_bad_symbol_and_always_prints_one_json_document(capsys):
    out = io.StringIO()
    with redirect_stdout(out):
        assert page.main(["bad;symbol"]) == 0
    assert json.loads(out.getvalue()) == {"error": "bad symbol"}
