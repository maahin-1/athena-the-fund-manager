from datetime import date, datetime, timedelta, timezone

from athena.evidence import verify as vf
from athena.evidence.store import EvidenceStore
from athena.news.model import NewsItem

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
NAMES = [("INFY", "Infosys Limited"), ("TCS", "Tata Consultancy Services Limited")]
IST = timezone(timedelta(hours=5, minutes=30))


class Data:
    """Prices for INFY only: Tuesday close 100, Wednesday high 106."""

    def __init__(self, bars=True):
        self._on = bars

    def bars(self, symbol, since, until):
        if not self._on:
            return []
        return [(d, o, h, low, c) for d, o, h, low, c in [(date(2026, 10, 27), 100, 101, 99, 100), (date(2026, 10, 28), 100, 106, 99, 103)] if since <= d <= until]

    def last_bar_day(self, symbol):
        return date(2026, 10, 28) if self._on else None

    def quarters(self, symbol):
        return []

    def insider(self, symbol, since, until):
        return []

    def insider_newest(self):
        return None

    def flows(self, since, until):
        return []

    def flows_first(self):
        return None

    def board(self, symbol, since, until):
        return []


def put(store, source, title, when=datetime(2026, 10, 28, 5, 30, tzinfo=timezone.utc)):  # 11:00 India time on the 28th
    store.add_items(source, [NewsItem(title, "Pub", when, f"https://x/{abs(hash(title))}", "publisher feed", "")], NOW)


def test_a_pass_reads_new_items_once_keeps_their_claims_and_settles_what_can_be_settled(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "et_stocks", "Infosys shares jump 5% on a large deal")
    put(store, "mint_markets", "Infosys stock falls 3% after a downgrade")
    put(store, "bs_markets", "Maruti launches a new car")
    first = vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    assert first["read"] == 3 and first["claims"] == 2 and first["outcomes"] == {"confirmed": 1, "contradicted": 1}
    again = vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    assert again["read"] == 0 and again["claims"] == 0 and again["outcomes"] == {}  # nothing is read twice and settled claims are not checked again
    record = store.source_record()
    assert record["et_stocks"] == {"confirmed": 1, "partly": 0, "contradicted": 0} and record["mint_markets"]["contradicted"] == 1


def test_a_claim_waiting_for_data_is_asked_again_and_a_settled_check_is_never_overwritten(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "et_stocks", "Infosys shares jump 5% on a large deal", datetime(2026, 10, 29, 5, 30, tzinfo=timezone.utc))  # the 29th: its bar is not loaded
    first = vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    assert first["outcomes"] == {"pending": 1}
    assert store.source_record() == {}  # a pending claim says nothing about its source
    assert vf.check_open(store, Data(), NOW) == {"pending": 1}  # still open, asked again
    (claim,) = store.claims_to_check(NOW - timedelta(days=30))
    store.save_checks([(claim["claim_id"], "confirmed", True, "settled", {})], NOW)
    store.save_checks([(claim["claim_id"], "contradicted", True, "should not replace", {})], NOW)
    assert store.claims_to_check(NOW - timedelta(days=30)) == [] and store.source_record()["et_stocks"]["confirmed"] == 1


def test_a_store_that_cannot_be_opened_leaves_its_claims_open_instead_of_failing_them(tmp_path):
    from athena.contracts import AthenaError

    class Down(Data):
        def last_bar_day(self, symbol):
            raise AthenaError("the warehouse could not be opened")

    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "et_stocks", "Infosys shares jump 5% on a large deal")
    out = vf.run(store, NAMES, Down(), NOW, log=lambda s: None)
    assert out["outcomes"] == {"waiting": 1} and store.source_record() == {}
    assert len(store.claims_to_check(NOW - timedelta(days=30))) == 1


def test_claims_and_checks_can_be_cleared_and_rebuilt_from_the_items_which_are_never_touched(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "et_stocks", "Infosys shares jump 5% on a large deal")
    vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    store.reset_claims()
    assert store.claim_counts()["claims"] == 0 and len(store.items_between(NOW - timedelta(days=5), NOW)) == 1
    assert vf.run(store, NAMES, Data(), NOW, log=lambda s: None)["claims"] == 1


def test_claims_are_listed_for_a_company_with_the_check_beside_each(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "et_stocks", "Infosys shares jump 5% on a large deal")
    put(store, "mint_markets", "Infosys in talks to buy a German consultancy, sources said")
    vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    got = store.claims_for("INFY", NOW - timedelta(days=30), NOW)
    assert {(r["kind"], r["outcome"]) for r in got} == {("price_move", "confirmed"), ("rumour", "unverified")}
    price = next(r for r in got if r["kind"] == "price_move")
    assert price["fields"] == {"direction": 1, "pct": 5.0} and price["ours"]["session"] == "2026-10-28" and price["link"].startswith("https://")
    assert store.claims_for("TCS", NOW - timedelta(days=30), NOW) == []


def test_the_page_shows_contradicted_claims_first_with_what_our_data_said_and_each_sources_record(tmp_path):
    from types import SimpleNamespace

    from athena.sentiment import page
    from athena.sentiment.reddit import RedditSearch

    class Hub:
        def fetch(self, symbol, company, since, until):
            return SimpleNamespace(items=[], notes=())

    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "et_stocks", "Infosys shares jump 5% on a large deal")
    put(store, "mint_markets", "Infosys stock falls 3% after a downgrade")
    put(store, "bs_markets", "Infosys in talks to buy a German consultancy, sources said")
    put(store, "bl_markets", "Infosys board to meet on 5 Nov to consider dividend")
    store.record_health("et_stocks", NOW, True, "ok", "", 1, NOW)
    vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    doc = page.build_page("INFY", "Infosys Limited", date(2026, 10, 29), Hub(), RedditSearch(None), None, store)
    box = doc["claims"]
    assert [c["text"] for c in box["conflicts"]] == ["Infosys stock falls 3%"] and "furthest it fell" in box["conflicts"][0]["detail"]
    assert [c["text"] for c in box["confirmed"]] == ["Infosys shares jump 5%"]
    assert len(box["disagreements"]) == 1 and {s["outcome"] for s in box["disagreements"][0]["sides"]} == {"confirmed", "contradicted"}
    assert box["unconfirmed"][0]["stories"] == 1 and "no filing or price in our data confirms it" in box["unconfirmed"][0]["note"]
    assert box["counts"]["confirmed"] == 1 and box["counts"]["contradicted"] == 1 and box["counts"]["pending"] == 1  # the board-meeting claim waits: no notice held yet
    assert "by_item" not in box
    rows = {r["title"]: r for r in doc["news"]["items"]}
    assert rows["Infosys stock falls 3% after a downgrade"]["claim"]["outcome"] == "contradicted"
    sources = {s["id"]: s for s in doc["sources"]}
    assert sources["et_stocks"]["trust"]["confirmed"] == 1 and sources["et_stocks"]["trust"]["basis"] == "prior" and sources["mint_markets"]["trust"]["value"] < sources["et_stocks"]["trust"]["value"]


def test_a_flow_claim_has_no_company_and_is_listed_as_market_wide(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    put(store, "cnbctv18_markets", "FIIs sold shares worth Rs 3,500 crore on Tuesday")
    vf.run(store, NAMES, Data(), NOW, log=lambda s: None)
    got = store.market_claims(NOW - timedelta(days=30), NOW)
    assert len(got) == 1 and got[0]["kind"] == "flow" and got[0]["outcome"] == "not_checkable"
