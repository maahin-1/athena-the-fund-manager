from datetime import datetime, timezone

import pytest

from athena.contracts import EmptyRefreshError, Record, StaleDataError, UnknownInstrument
from athena.isin import isin_check_digit
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import (
    MAX_CANDIDATES,
    MAX_LISTED,
    Ambiguity,
    Candidate,
    InstrumentIndex,
    InstrumentResolver,
    Resolution,
    more_candidates_line,
    normalize_input,
    normalize_name,
)
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)

EQUITIES = {
    "SBIN": ("State Bank of India", "INE062A01020"),
    "SBILIFE": ("SBI Life Insurance Company Limited", "INE123W01016"),
    "SBICARD": ("SBI Cards and Payment Services Limited", "INE018E01016"),
    "TCS": ("Tata Consultancy Services Limited", "INE467B01029"),
    "RELIANCE": ("Reliance Industries Limited", "INE002A01018"),
    "INFY": ("Infosys Limited", "INE009A01021"),
    "M&M": ("Mahindra & Mahindra Limited", "INE101A01026"),
    "BAJAJ-AUTO": ("Bajaj Auto Limited", "INE917I01010"),
    "TATAMOTORS": ("Tata Motors Limited", "INE155A01022"),
    "TATASTEEL": ("Tata Steel Limited", "INE081A01020"),
    "NIFTYBEES": ("Shadow Equity With ETF Symbol", "INE000000000"),
}
ETFS = {
    "NIFTYBEES": ("NIPINDETFNIFTYBEES", "INF204KB14I2"),
    "BANKBEES": ("NIPPON INDIA ETF BANK BEES", "INF204KB15I9"),
}


def make_store(equity_as_of=NOW, etf_as_of=NOW):
    store = DataStore()
    for symbol, (name, isin) in EQUITIES.items():
        store.put(Record(EQUITY_DATASET, symbol, equity_as_of, "nse.archives", {"name": name, "series": "EQ", "isin": isin, "listing_date": "x"}))
    for symbol, (name, isin) in ETFS.items():
        store.put(Record(ETF_DATASET, symbol, etf_as_of, "nse.archives", {"name": name, "isin": isin, "underlying": "u", "underlying_class": "EQUITY", "underlying_key": "k"}))
    return store


@pytest.fixture
def resolver():
    return InstrumentResolver(InstrumentIndex.from_store(make_store(), now=NOW))


def test_normalizers():
    assert normalize_input("  nse:sbin.ns ") == "SBIN"
    assert normalize_input("m&m") == "M&M"
    assert normalize_name("Mahindra & Mahindra Limited") == "mahindra and mahindra"
    assert normalize_name("Tata Consultancy Services Ltd.") == "tata consultancy services"


@pytest.mark.parametrize("query", ["SBIN", "sbin", " SBIN.NS ", "NSE:SBIN"])
def test_ticker_variants_resolve_exactly(resolver, query):
    result = resolver.resolve(query)
    assert isinstance(result, Resolution)
    assert (result.asset_class, result.identifier, result.identifier_type, result.resolution_path, result.confidence) == (
        "equity", "SBIN", "ticker", "exact", 1.0,
    )
    assert result.routed_specialists == ("valuation", "moat_quality", "quant_technical", "earnings_intelligence")


@pytest.mark.parametrize("query, symbol", [("m&m", "M&M"), ("bajaj-auto", "BAJAJ-AUTO")])
def test_symbols_with_punctuation(resolver, query, symbol):
    assert resolver.resolve(query).identifier == symbol


def test_etf_beats_equity_with_the_same_symbol(resolver):
    result = resolver.resolve("NIFTYBEES")
    assert (result.asset_class, result.isin) == ("etf", "INF204KB14I2")
    assert result.routed_specialists == ("etf_analyst", "quant_technical")


def test_isin_resolves_to_equity_and_etf(resolver):
    equity = resolver.resolve("INE062A01020")
    etf = resolver.resolve("inf204kb14i2")
    assert (equity.asset_class, equity.identifier, equity.identifier_type) == ("equity", "SBIN", "isin")
    assert (etf.asset_class, etf.identifier) == ("etf", "NIFTYBEES")


def test_bad_isin_checksum_is_rejected(resolver):
    with pytest.raises(UnknownInstrument, match="check digit"):
        resolver.resolve("INE062A01021")


def test_unlisted_fund_isin_explains_mutual_funds_are_unsupported(resolver):
    body = "INF000Z99ZZ"
    with pytest.raises(UnknownInstrument, match="mutual-fund support is not available"):
        resolver.resolve(body + str(isin_check_digit(body)))


def test_unlisted_corporate_isin_is_unknown(resolver):
    body = "INE000Z99ZZ"
    with pytest.raises(UnknownInstrument, match="not in the NSE equity or ETF lists"):
        resolver.resolve(body + str(isin_check_digit(body)))


@pytest.mark.parametrize(
    "query, symbol",
    [("State Bank of India", "SBIN"), ("tata consultancy services", "TCS"), ("Mahindra and Mahindra", "M&M"), ("Infosys Ltd", "INFY")],
)
def test_exact_names_after_normalisation(resolver, query, symbol):
    result = resolver.resolve(query)
    assert (result.identifier, result.identifier_type, result.resolution_path) == (symbol, "name", "exact")


@pytest.mark.parametrize(
    "query, symbol",
    [("Relience Industries", "RELIANCE"), ("RELIANC", "RELIANCE"), ("Infosyss", "INFY"), ("Tata Steal", "TATASTEEL")],
)
def test_typos_resolve_fuzzily(resolver, query, symbol):
    result = resolver.resolve(query)
    assert isinstance(result, Resolution), result
    assert (result.identifier, result.resolution_path) == (symbol, "fuzzy")
    assert 0.9 <= result.confidence < 1.0


def test_ambiguous_prefix_returns_candidates_not_a_guess(resolver):
    result = resolver.resolve("SBI")
    assert isinstance(result, Ambiguity)
    assert {c.identifier for c in result.candidates} >= {"SBIN", "SBILIFE", "SBICARD"}
    assert result.candidates[0].score >= result.candidates[-1].score


def test_unknown_input_raises(resolver):
    with pytest.raises(UnknownInstrument, match="ZZZZQQ"):
        resolver.resolve("ZZZZQQ")


def test_empty_query_is_a_value_error(resolver):
    with pytest.raises(ValueError, match="empty"):
        resolver.resolve("   ")


class FakeClassifier:
    def __init__(self, index=0, probability=0.9, error=None):
        self.index, self.probability, self.error, self.seen = index, probability, error, []

    def choose(self, query, candidates):
        self.seen.append((query, list(candidates)))
        if self.error:
            raise self.error
        return self.index, self.probability


def classified(classifier):
    return InstrumentResolver(InstrumentIndex.from_store(make_store(), now=NOW), classifier)


def test_classifier_resolves_ambiguity_when_confident():
    fake = FakeClassifier(index=0, probability=0.9)
    result = classified(fake).resolve("SBI")
    assert isinstance(result, Resolution)
    assert (result.resolution_path, result.confidence) == ("model", 0.9)
    assert result.identifier == fake.seen[0][1][0].identifier
    assert all(isinstance(c, Candidate) for c in fake.seen[0][1])


def test_low_confidence_or_failing_classifier_falls_back_to_asking_the_user():
    assert isinstance(classified(FakeClassifier(probability=0.5)).resolve("SBI"), Ambiguity)
    assert isinstance(classified(FakeClassifier(error=RuntimeError("down"))).resolve("SBI"), Ambiguity)
    assert isinstance(classified(FakeClassifier(index=99, probability=0.99)).resolve("SBI"), Ambiguity)


def test_classifier_is_not_called_for_clear_matches():
    fake = FakeClassifier()
    classified(fake).resolve("SBIN")
    classified(fake).resolve("Relience Industries")
    assert fake.seen == []


def test_confirm_turns_a_chosen_candidate_into_a_resolution(resolver):
    ambiguity = resolver.resolve("SBI")
    index = [c.identifier for c in ambiguity.candidates].index("SBILIFE")
    result = resolver.confirm(ambiguity, index)
    assert (result.identifier, result.resolution_path, result.confidence) == ("SBILIFE", "user_confirmed", 1.0)


def test_index_refuses_stale_masters():
    old = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    with pytest.raises(StaleDataError, match="master.nse_equity"):
        InstrumentIndex.from_store(make_store(equity_as_of=old), now=NOW)


def test_index_requires_both_masters():
    store = DataStore()
    store.put(Record(EQUITY_DATASET, "SBIN", NOW, "nse.archives", {"name": "State Bank of India", "isin": "INE062A01020"}))
    with pytest.raises(EmptyRefreshError, match="master.nse_etf"):
        InstrumentIndex.from_store(store, now=NOW)


def _mini_resolver():
    store = DataStore()
    for symbol, name in (("SUNDARAM", "Sundaram Brake Linings Limited"), ("SOUND", "Sound Systems Limited"), ("SUNTV", "Sun TV Network Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "nse.archives", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "nse.archives", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def test_prefix_matches_rank_ahead_of_look_alikes_and_are_never_auto_accepted():
    result = _mini_resolver().resolve("SUND")
    assert isinstance(result, Ambiguity)  # a prefix is not a name, so it is never accepted on its own
    identifiers = [c.identifier for c in result.candidates]
    assert identifiers[0] == "SUNDARAM"
    assert "SOUND" in identifiers and identifiers.index("SUNDARAM") < identifiers.index("SOUND")


def test_name_prefix_surfaces_the_group_for_a_short_ambiguous_query():
    result = resolver_for_tata().resolve("TATA")
    assert isinstance(result, Ambiguity)
    assert {"TATAMOTORS", "TATASTEEL", "TCS"} <= {c.identifier for c in result.candidates}


def resolver_for_tata():
    return InstrumentResolver(InstrumentIndex.from_store(make_store(), now=NOW))


def many_listings(count, name="Alpha Industries {n:03d} Limited"):
    """A master list where `count` equities share a name stem, plus the one ETF the index requires."""
    store = DataStore()
    for n in range(count):
        store.put(Record(EQUITY_DATASET, f"ALPHA{n:03d}", NOW, "nse.archives", {"name": name.format(n=n), "series": "EQ", "isin": "INE000000000", "listing_date": "x"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "nse.archives", {"name": "NIPINDETFNIFTYBEES", "isin": "INF204KB14I2", "underlying": "u", "underlying_class": "EQUITY", "underlying_key": "k"}))
    return store


def test_a_short_query_lists_every_match_up_to_the_listing_limit():
    twelve = InstrumentResolver(InstrumentIndex.from_store(many_listings(12), now=NOW)).resolve("alpha")
    assert isinstance(twelve, Ambiguity) and len(twelve.candidates) == 12 > MAX_CANDIDATES
    sixty = InstrumentResolver(InstrumentIndex.from_store(many_listings(60), now=NOW)).resolve("alpha")
    assert isinstance(sixty, Ambiguity) and len(sixty.candidates) == MAX_LISTED == 50
    scores = [c.score for c in sixty.candidates]
    assert scores == sorted(scores, reverse=True)  # still ranked, best first


def test_a_name_shared_by_many_listings_lists_them_all_up_to_the_limit():
    shared = InstrumentResolver(InstrumentIndex.from_store(many_listings(55, name="Shared Name Limited"), now=NOW))
    result = shared.resolve("Shared Name Limited")
    assert isinstance(result, Ambiguity) and len(result.candidates) == MAX_LISTED
    assert result.reason == "the name matches more than one instrument"


def test_the_model_classifier_and_a_resolution_still_see_only_the_top_five():
    fake = FakeClassifier(index=0, probability=0.9)
    result = InstrumentResolver(InstrumentIndex.from_store(many_listings(60), now=NOW), fake).resolve("alpha")
    assert isinstance(result, Resolution) and result.resolution_path == "model"
    assert len(fake.seen[0][1]) == MAX_CANDIDATES and len(result.candidates) == MAX_CANDIDATES


def test_confirm_accepts_any_candidate_on_the_long_list():
    resolver = InstrumentResolver(InstrumentIndex.from_store(many_listings(60), now=NOW))
    ambiguity = resolver.resolve("alpha")
    chosen = resolver.confirm(ambiguity, 30)
    assert chosen.identifier == ambiguity.candidates[30].identifier and chosen.resolution_path == "user_confirmed"


def test_a_text_list_says_how_many_more_there_are():
    assert more_candidates_line(25, 10) == "  ...and 15 more; type the exact symbol."
    assert more_candidates_line(10, 10) is None and more_candidates_line(3, 3) is None


def careful(*extra, classifier=None):
    """A resolver for a person at a screen, over the test listings plus any extra (symbol, name, isin) equities."""
    store = make_store()
    for symbol, name, isin in extra:
        store.put(Record(EQUITY_DATASET, symbol, NOW, "nse.archives", {"name": name, "series": "EQ", "isin": isin, "listing_date": "x"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW), classifier, confirm_related=True)


RPOWER = ("RPOWER", "Reliance Power Limited", "INE614G01033")


@pytest.mark.parametrize("query", ["SBIN", "sbin", "NSE:SBIN.NS"])
def test_a_typed_ticker_or_isin_is_still_taken_as_it_is_when_the_person_confirms_related(query):
    result = careful().resolve(query)
    assert isinstance(result, Resolution) and (result.identifier, result.resolution_path) == ("SBIN", "exact")
    assert careful().resolve("INE062A01020").identifier == "SBIN"


def test_a_typo_is_listed_with_its_relatives_not_picked_for_the_person():
    result = careful().resolve("Tata Steal")
    assert isinstance(result, Ambiguity)
    assert [c.identifier for c in result.candidates][:2] == ["TATASTEEL", "TATAMOTORS"]
    assert careful().resolve("Tata Steal").reason == "other instruments are related to what you typed"


def test_an_exact_name_with_a_relative_is_listed_with_the_exact_one_first():
    result = careful(RPOWER).resolve("Reliance Industries")
    assert isinstance(result, Ambiguity)
    assert [(c.identifier, c.score) for c in result.candidates][:2] == [("RELIANCE", 1.0), ("RPOWER", result.candidates[1].score)]
    assert result.reason == "other instruments are related to this name"
    assert len({c.identifier for c in result.candidates}) == len(result.candidates)  # nobody listed twice


def test_an_exact_name_nothing_else_resembles_is_taken_as_it_is():
    result = careful().resolve("Infosys Ltd")
    assert isinstance(result, Resolution) and (result.identifier, result.resolution_path) == ("INFY", "exact")


def test_a_model_never_settles_a_choice_when_the_person_confirms_related():
    fake = FakeClassifier(index=0, probability=0.99)
    assert isinstance(careful(classifier=fake).resolve("SBI"), Ambiguity)
    assert fake.seen == []


def test_a_weak_lone_match_is_still_listed_rather_than_picked():
    result = careful().resolve("Mahindra")
    assert isinstance(result, Ambiguity) and [c.identifier for c in result.candidates] == ["M&M"]
