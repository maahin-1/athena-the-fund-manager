import pytest

from athena.contracts import UnknownInstrument
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.resolver import Ambiguity, InstrumentIndex, InstrumentResolver, Resolution
from athena.store import DataStore

pytestmark = pytest.mark.live

EQUITY = "equity"
ETF = "etf"
AMBIGUOUS = "AMBIGUOUS"
UNKNOWN = "UNKNOWN"

# (query, expected) where expected is (asset_class, symbol), AMBIGUOUS or UNKNOWN.
CASES = [
    ("SBIN", (EQUITY, "SBIN")),
    ("reliance", (EQUITY, "RELIANCE")),
    ("TCS.NS", (EQUITY, "TCS")),
    ("NSE:INFY", (EQUITY, "INFY")),
    ("HDFCBANK", (EQUITY, "HDFCBANK")),
    ("M&M", (EQUITY, "M&M")),
    ("BAJAJ-AUTO", (EQUITY, "BAJAJ-AUTO")),
    ("ITC", (EQUITY, "ITC")),
    ("NIFTYBEES", (ETF, "NIFTYBEES")),
    ("JUNIORBEES", (ETF, "JUNIORBEES")),
    ("GOLDBEES", (ETF, "GOLDBEES")),
    ("BANKBEES", (ETF, "BANKBEES")),
    ("INE062A01020", (EQUITY, "SBIN")),
    ("INF204KB14I2", (ETF, "NIFTYBEES")),
    ("State Bank of India", (EQUITY, "SBIN")),
    ("Tata Consultancy Services", (EQUITY, "TCS")),
    ("Infosys", (EQUITY, "INFY")),
    ("Reliance Industries", (EQUITY, "RELIANCE")),
    ("Relience Industries", (EQUITY, "RELIANCE")),
    ("Hindustan Unilever", (EQUITY, "HINDUNILVR")),
    ("Larsen and Toubro", (EQUITY, "LT")),
    ("Asian Paints", (EQUITY, "ASIANPAINT")),
    ("RELIANC", (EQUITY, "RELIANCE")),
    ("SBI", AMBIGUOUS),
    ("TATA", AMBIGUOUS),
    ("nifty 50 etf", AMBIGUOUS),
    ("ZZZZQQ", UNKNOWN),
]


@pytest.fixture(scope="module")
def resolver():
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    return InstrumentResolver(InstrumentIndex.from_store(store))


@pytest.mark.parametrize("query, expected", CASES)
def test_live_resolver_labeled_cases(resolver, query, expected):
    try:
        result = resolver.resolve(query)
    except UnknownInstrument:
        assert expected == UNKNOWN
        return
    if isinstance(result, Ambiguity):
        assert expected == AMBIGUOUS
    else:
        assert isinstance(result, Resolution)
        assert expected == (result.asset_class, result.identifier)


def test_live_short_query_lists_more_than_five_candidates_and_any_of_them_can_be_confirmed(resolver):
    result = resolver.resolve("TATA")
    assert isinstance(result, Ambiguity) and 5 < len(result.candidates) <= 50
    chosen = resolver.confirm(result, len(result.candidates) - 1)
    assert chosen.identifier == result.candidates[-1].identifier and chosen.resolution_path == "user_confirmed"
