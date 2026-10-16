import pytest

from athena.fundamentals import universe as u

NIFTY50 = "Company Name,Industry,Symbol,Series,ISIN Code\nTata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
NIFTY500 = NIFTY50 + "Zomato Ltd.,Consumer Services,ZOMATO,EQ,INE758T01015\n"
EQUITY = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1\n"
    "ZOMATO,Zomato Limited,EQ,23-JUL-2021,1,1,INE758T01015,1\n"
    "TINYCO,Tiny Co Limited,BE,01-JAN-2010,10,1,INE000A01010,10\n"
    "SMECO,Sme Co Limited,SM,01-JAN-2020,10,1,INE000B01010,10\n"
    "BONDX,Bond X,N1,01-JAN-2020,1000,1,INE000C01010,1000\n"
)


def test_index_and_master_files_parse_to_members():
    nifty = u.parse_index_csv(NIFTY50)
    assert [m.symbol for m in nifty] == ["TCS", "HDFCBANK"]
    assert nifty[0].industry == "Information Technology" and nifty[0].isin == "INE467B01029"
    master = u.parse_equity_master(EQUITY)
    assert [m.symbol for m in master] == ["TCS", "ZOMATO", "TINYCO"]  # equity series only: no SME board, no bonds


def test_the_universe_is_ordered_nifty_50_then_nifty_500_then_the_rest_without_repeats():
    order = u.ordered(u.parse_index_csv(NIFTY50), u.parse_index_csv(NIFTY500), u.parse_equity_master(EQUITY))
    assert [m.symbol for m in order] == ["TCS", "HDFCBANK", "ZOMATO", "TINYCO"]
    assert order[0].industry == "Information Technology"  # the index file's industry is kept
    assert order[3].industry == ""  # a name outside the indices has none until its filings say


def test_named_universes_fetch_the_right_files_and_a_symbol_list_needs_no_fetch():
    calls = []

    def get(url, **kwargs):
        calls.append(url)

        class Response:
            status_code = 200
            text = {"nifty50": NIFTY50, "nifty500": NIFTY500, "EQUITY_L": EQUITY}[next(k for k in ("nifty500", "nifty50", "EQUITY_L") if k in url)]

        return Response()

    assert [m.symbol for m in u.universe("nifty50", get)] == ["TCS", "HDFCBANK"]
    assert [m.symbol for m in u.universe("all", get)] == ["TCS", "HDFCBANK", "ZOMATO", "TINYCO"]
    calls.clear()
    assert [m.symbol for m in u.universe("tcs, sbin", get)] == ["TCS", "SBIN"]
    assert calls == []


def test_a_refused_fetch_is_an_error():
    class Refused:
        status_code = 403
        text = ""

    with pytest.raises(RuntimeError):
        u.fetch_text("https://example.test/x.csv", lambda url, **kw: Refused())
