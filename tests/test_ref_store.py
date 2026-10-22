from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.reference.store import ReferenceStore, StatRow, open_read


def stat(industry="Steel", metric="beta", value=1.06, n=185, as_of=date(2026, 1, 5), dataset="beta"):
    return StatRow("damodaran", "India", dataset, industry, metric, value, n, as_of, f"{dataset}India.xls")


def test_an_input_is_the_newest_dated_on_or_before_the_asked_date_and_none_before_the_first():
    with ReferenceStore(":memory:") as store:
        store.put_input("tax_rate", date(2025, 4, 1), 0.30, "owner")
        store.put_input("tax_rate", date(2026, 4, 1), 0.2517, "owner", "concessional regime")
        assert store.input("tax_rate", date(2026, 3, 31)).value == 0.30
        latest = store.input("tax_rate", date(2026, 10, 22))
        assert (latest.value, latest.as_of, latest.note) == (0.2517, date(2026, 4, 1), "concessional regime")
        assert store.input("tax_rate", date(2025, 3, 31)) is None  # not defaulted: the caller must say it is missing
        assert store.input("nothing", date(2026, 1, 1)) is None


def test_loading_the_same_rows_twice_leaves_one_copy_and_a_new_value_replaces_the_old():
    with ReferenceStore(":memory:") as store:
        store.put_stats([stat(), stat(metric="unlevered_beta", value=0.9)])
        store.put_stats([stat(value=1.1)])
        stats = store.industry_stats("Steel", on_or_before=date(2026, 6, 1))
        assert stats == {"beta.beta": (1.1, 185), "beta.unlevered_beta": (0.9, 185)}


def test_stats_come_from_the_newest_dataset_published_on_or_before_the_date():
    with ReferenceStore(":memory:") as store:
        store.put_stats([stat(value=0.8, as_of=date(2025, 1, 6)), stat(value=1.06, as_of=date(2026, 1, 5))])
        assert store.industry_stats("Steel", on_or_before=date(2025, 12, 31))["beta.beta"][0] == 0.8
        assert store.industry_stats("Steel")["beta.beta"][0] == 1.06
        assert store.industry_stats("Steel", on_or_before=date(2024, 12, 31)) == {}  # a dataset published later is never used for an earlier date
        assert store.industry_stats("Unknown industry") == {}


def test_company_map_and_country_premium_lookups():
    with ReferenceStore(":memory:") as store:
        store.put_company_industries([("TCS", "Computer Services", "nse_ticker", 1.0, date(2026, 1, 5)), ("XYZ", "Steel", "crosswalk", 0.6, date(2026, 1, 5))])
        assert store.company_industry("TCS") == ("Computer Services", "nse_ticker", 1.0)
        assert store.company_industry("NOPE") is None
        assert store.company_industry_counts() == {"nse_ticker": 1, "crosswalk": 1}
        store.put_country_premiums([("India", "Asia", "Baa3", 0.0187, 0.0708, 0.0285, date(2026, 1, 1))])
        assert store.country_premium("India")[:4] == ("Baa3", 0.0187, 0.0708, 0.0285)
        assert store.country_premium("India", date(2025, 12, 31)) is None


def test_a_missing_store_is_reported_and_a_read_only_store_sees_what_was_written(tmp_path):
    with pytest.raises(AthenaError):
        open_read(tmp_path / "none.duckdb")
    path = tmp_path / "ref.duckdb"
    with ReferenceStore(path) as store:
        store.put_input("erp_mature", date(2026, 1, 1), 0.0423, "damodaran")
    with open_read(path) as store:
        assert store.input("erp_mature", date(2026, 6, 1)).value == 0.0423
