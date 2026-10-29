from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from athena.evidence.store import EvidenceStore
from athena.news.model import NewsItem
from athena.sentiment import page
from athena.sentiment.reddit import RedditSearch

NOW = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 29)


def stored(tmp_path, items_by_source):
    store = EvidenceStore(tmp_path / "e.duckdb")
    for source_id, items in items_by_source.items():
        store.add_items(source_id, items, NOW)
    return store


def news(title, hours_ago=2.0, publisher="Mint", link=None):
    return NewsItem(title, publisher, NOW - timedelta(hours=hours_ago), link or f"https://x/{abs(hash(title))}", "publisher feed", "")


class Hub:
    def __init__(self, items=()):
        self.items = list(items)

    def fetch(self, symbol, company, since, until):
        return SimpleNamespace(items=self.items, notes=())


def reddit_off():
    return RedditSearch(None)


def test_stored_items_that_name_the_company_become_cited_rows_and_others_do_not(tmp_path):
    store = stored(tmp_path, {"mint_markets": [news("Infosys wins a large deal from a European bank"), news("Maruti launches a new hatchback"), news("Sensex ends higher as banks rally")]})
    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub(), reddit_off(), None, store)
    rows = doc["news"]["items"]
    assert [r["title"] for r in rows] == ["Infosys wins a large deal from a European bank"]
    r = rows[0]
    assert r["source_id"] == "mint_markets" and r["tier"] == "B" and r["publisher"] == "Mint" and r["link"].startswith("https://")
    assert r["published"] and r["first_seen"] and r["story_id"]  # the citation: who, where, when published, when first seen


def test_an_item_the_older_path_already_found_is_not_shown_twice(tmp_path):
    title = "Infosys wins a large deal from a European bank"
    store = stored(tmp_path, {"et_markets": [news(title, 1.0, "Economic Times")]})
    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub([news(title, 2.0, "Mint", "https://other")]), reddit_off(), None, store)
    assert len(doc["news"]["items"]) == 1


def test_regulator_items_go_to_market_and_policy_and_press_items_do_not(tmp_path):
    store = stored(tmp_path, {"sebi": [NewsItem("SEBI order in the matter of a broker", "SEBI", NOW - timedelta(hours=3), "https://sebi/1", "regulator", "")], "mint_markets": [news("Sensex ends higher as banks rally")]})
    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub(), reddit_off(), None, store)
    assert [m["title"] for m in doc["market"]] == ["SEBI order in the matter of a broker"] and doc["market"][0]["tier"] == "A"


def test_the_sources_list_says_what_was_read_what_is_off_and_why_and_how_many_rows_each_gave(tmp_path):
    store = stored(tmp_path, {"mint_markets": [news("Infosys wins a large deal from a European bank")]})
    store.record_health("mint_markets", NOW, True, "ok", "", 1, NOW)
    store.record_health("bs_markets", NOW, False, "failed", "HTTP 503")
    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub(), reddit_off(), None, store)
    by = {s["id"]: s for s in doc["sources"]}
    assert by["mint_markets"]["status"] == "ok" and by["mint_markets"]["rows_here"] == 1 and by["mint_markets"]["held"] == 1
    assert by["bs_markets"]["status"] == "failed" and by["bs_markets"]["reason"] == "HTTP 503"
    assert by["moneycontrol"]["status"] == "off" and "2024-04-23" in by["moneycontrol"]["reason"]
    assert by["et_stocks"]["status"] == "never tried" and by["nse"]["tier"] == "A"


def test_without_a_store_the_page_is_what_it_was_and_a_broken_store_adds_a_note_not_an_error():
    plain = page.build_page("INFY", "Infosys Limited", TODAY, Hub([news("Infosys wins a large deal from a European bank")]), reddit_off())
    assert plain["market"] == [] and plain["sources"] == [] and len(plain["news"]["items"]) == 1
    assert plain["news"]["items"][0]["tier"] == "B" and plain["news"]["items"][0]["source_id"] == "mint"

    class Broken:
        def items_between(self, *a, **k):
            raise OSError("locked")

    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub([news("Infosys wins a large deal from a European bank")]), reddit_off(), None, Broken())
    assert len(doc["news"]["items"]) == 1 and any("stored sources unavailable" in n for n in doc["news"]["notes"])


def test_press_comes_from_the_store_once_the_collector_has_worked_and_from_the_live_path_before(tmp_path):
    live = Hub([news("Infosys wins a large deal from a European bank", 2.0, "Mint", "https://live")])
    empty = EvidenceStore(tmp_path / "a.duckdb")
    assert [r["source_id"] for r in page.build_page("INFY", "Infosys Limited", TODAY, live, reddit_off(), None, empty)["news"]["items"]] == ["mint"]  # nothing collected yet: the live rows stay
    worked = stored(tmp_path / "b", {"mint_markets": [news("Infosys wins a large deal from a European bank", 1.0, "Mint", "https://stored")]})
    worked.record_health("mint_markets", NOW, True, "ok", "", 1, NOW)
    rows = page.build_page("INFY", "Infosys Limited", TODAY, live, reddit_off(), None, worked)["news"]["items"]
    assert [r["source_id"] for r in rows] == ["mint_markets"]  # the store has it, with a source a chip can name


def test_an_acronym_built_from_the_name_is_matched_whole_and_in_capitals_only(tmp_path):
    assert page.acronym("State Bank of India") == "SBI" and page.acronym("Tata Consultancy Services Limited") == "TCS" and page.acronym("Oil and Natural Gas Corporation Limited") == "ONGC"
    assert page.acronym("Infosys Limited") == "" and page.acronym("Reliance Industries Limited") == "" and page.acronym("Bank of Baroda") == ""
    store = stored(tmp_path, {"mint_markets": [news("SBI raises lending rates by 10 bps across tenors"), news("Rising prices hit consumers at the pump this week"), news("Sbi group stocks slide as market falls"), news("SBICARDS posts strong quarter on spends")]})
    doc = page.build_page("SBIN", "State Bank of India", TODAY, Hub(), reddit_off(), None, store)
    assert [r["title"] for r in doc["news"]["items"]] == ["SBI raises lending rates by 10 bps across tenors"]
