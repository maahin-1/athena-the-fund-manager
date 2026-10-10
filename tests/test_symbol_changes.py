from datetime import date

import pytest
from bar_factory import NOW

from athena.contracts import AthenaError, Record
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore
from athena.symbol_changes import SymbolChanges, describe_trail, parse_symbol_changes

CSV = """\
NIPPON INDIA MF - NIPPON INDIA Dual Advantage FTF  Sr. x Plan E -  GO,RDAXEDG,NDAXEDG,30-OCT-2019
ETERNAL LIMITED,ZOMATO,ETERNAL,09-APR-2025
Tata Motors Limited,TELCO,TATAMOTORS,26-DEC-2003
Tata Motors Passenger Vehicles Limited,TATAMOTORS,TMPV,24-OCT-2025
Acme, Holdings Limited,OLDACME,MIDACME,01-JAN-2020
Acme, Holdings Limited,MIDACME,ACME,01-JAN-2022
Loop Ltd,LOOPA,LOOPB,01-JAN-2020
Loop Ltd,LOOPB,LOOPA,01-JAN-2021
bad line without enough commas
Name,SYM,NEW,not a date
"""


def test_the_file_is_read_from_the_right_so_a_company_name_with_commas_survives_and_bad_rows_are_skipped():
    renames = parse_symbol_changes(CSV)
    assert renames["ZOMATO"][0].new == "ETERNAL" and renames["ZOMATO"][0].day == date(2025, 4, 9)
    assert renames["OLDACME"][0].company == "Acme, Holdings Limited"
    assert "SYM" not in renames and len(renames["TELCO"]) == 1


def changes():
    return SymbolChanges(lambda: parse_symbol_changes(CSV))


def test_a_retired_ticker_is_followed_to_the_symbol_that_is_listed_now():
    trail = changes().follow("zomato", lambda s: s == "ETERNAL", today=date(2026, 10, 10))
    assert [(r.old, r.new) for r in trail] == [("ZOMATO", "ETERNAL")]


def test_several_renames_in_a_row_are_followed_and_described_end_to_end():
    trail = changes().follow("OLDACME", lambda s: s == "ACME", today=date(2026, 1, 1))
    assert [r.new for r in trail] == ["MIDACME", "ACME"]
    assert describe_trail(trail) == "OLDACME is a retired ticker: the exchange renamed it (OLDACME -> MIDACME -> ACME, Acme, Holdings Limited, on 2022-01-01); showing ACME."


def test_a_symbol_the_exchange_reused_follows_its_latest_rename_away():
    trail = changes().follow("TATAMOTORS", lambda s: s == "TMPV", today=date(2026, 1, 1))
    assert [r.new for r in trail] == ["TMPV"]


def test_a_rename_in_the_future_or_to_a_symbol_that_is_not_listed_or_a_loop_claims_nothing():
    assert changes().follow("TATAMOTORS", lambda s: s == "TMPV", today=date(2025, 1, 1)) == []
    assert changes().follow("ZOMATO", lambda s: False, today=date(2026, 1, 1)) == []
    assert changes().follow("LOOPA", lambda s: False, today=date(2026, 1, 1)) == []
    assert changes().follow("NEVER", lambda s: True) == []


def test_the_list_is_fetched_once_and_a_failed_fetch_is_an_error_the_caller_can_catch():
    calls = []

    def fetch():
        calls.append(1)
        return parse_symbol_changes(CSV)

    shared = SymbolChanges(fetch)
    shared.follow("ZOMATO", lambda s: True)
    shared.follow("TELCO", lambda s: True)
    assert calls == [1]

    def broken():
        raise AthenaError("NSE symbol changes unavailable: HTTP 403")

    with pytest.raises(AthenaError):
        SymbolChanges(broken).follow("ZOMATO", lambda s: True)


# ---- through the resolver

def resolver_with(symbol_changes):
    store = DataStore()
    for symbol, name in (("ETERNAL", "ETERNAL LIMITED"), ("TMPV", "Tata Motors Passenger Vehicles Limited"), ("OMAXAUTO", "Omax Autos Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW), symbol_changes=symbol_changes)


def test_a_retired_ticker_resolves_to_its_new_symbol_with_a_note_not_to_a_similar_name():
    result = resolver_with(changes()).resolve("ZOMATO")
    assert (result.identifier, result.resolution_path, result.routed_specialists != ()) == ("ETERNAL", "renamed", True)
    assert "ZOMATO is a retired ticker" in result.note and "showing ETERNAL" in result.note


def test_a_listed_symbol_is_never_followed_and_a_normal_resolution_has_no_note():
    result = resolver_with(changes()).resolve("ETERNAL")
    assert result.resolution_path == "exact" and result.note == ""


def test_without_a_rename_list_or_when_it_cannot_be_loaded_the_old_behaviour_stands():
    def broken():
        raise AthenaError("blocked")

    for source in (None, SymbolChanges(broken)):
        result = resolver_with(source).resolve("ZOMATO")
        assert getattr(result, "identifier", None) != "ETERNAL"  # an ambiguity or a fuzzy answer, never a guessed rename
