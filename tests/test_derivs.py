import io
import zipfile
from datetime import date

import pytest

from athena.contracts import AthenaError, SchemaChangedError
from athena.derivs import bhav, ingest
from athena.derivs.store import DerivsStore

OLD = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,
FUTSTK,ABC,28-Aug-2023,0,XX,100,102,99,101,101.5,500,1,1000,50,01-AUG-2023,
FUTSTK,ABC,25-Sep-2023,0,XX,100,102,99,102,102.5,100,1,300,20,01-AUG-2023,
FUTSTK,ABC,26-Oct-2023,0,XX,100,102,99,103,103.5,10,1,30,2,01-AUG-2023,
FUTIDX,NIFTY,28-Aug-2023,0,XX,19000,19100,18900,19050,19050,900,1,5000,100,01-AUG-2023,
OPTSTK,ABC,28-Aug-2023,100,CE,5,6,4,5,5,40,1,700,10,01-AUG-2023,
OPTSTK,ABC,28-Aug-2023,100,PE,4,5,3,4,4,30,1,900,10,01-AUG-2023,
OPTSTK,ABC,28-Aug-2023,110,CE,2,3,1,2,2,10,1,0,0,01-AUG-2023,
OPTSTK,ABC,25-Sep-2023,100,CE,7,8,6,7,7,5,1,100,1,01-AUG-2023,
OPTIDX,NIFTY,28-Aug-2023,19000,PE,50,60,40,55,55,100,1,2000,5,01-AUG-2023,
"""
NEW = """FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,ClsPric,SttlmPric,TtlTradgVol,OpnIntrst,ChngInOpnIntrst
STF,ABC,2024-08-29,,,101,101.5,500,1000,50
STO,ABC,2024-08-29,100,CE,5,5,40,700,10
STO,ABC,2024-08-29,100,PE,4,4,30,900,10
"""
DAY = date(2023, 8, 1)


def test_both_file_layouts_are_read_into_the_same_rows():
    old, new = bhav.parse(OLD), bhav.parse(NEW)
    assert len(old) == 9 and old[0] == bhav.Contract("FUTSTK", "ABC", date(2023, 8, 28), 0.0, "", 101.0, 101.5, 500.0, 1000.0, 50.0)
    assert [(c.instrument, c.option, c.strike, c.oi) for c in new] == [("FUTSTK", "", 0.0, 1000.0), ("OPTSTK", "CE", 100.0, 700.0), ("OPTSTK", "PE", 100.0, 900.0)]
    with pytest.raises(SchemaChangedError):
        bhav.parse("a,b\n1,2\n")


def test_the_reduced_day_keeps_the_nearest_two_futures_expiries_option_totals_and_the_nearest_expiry_strikes_with_open_interest():
    got = bhav.reduce(bhav.parse(OLD), DAY, strikes=True)
    assert sorted((r[0], r[2].isoformat()) for r in got["futures"]) == [("ABC", "2023-08-28"), ("ABC", "2023-09-25"), ("NIFTY", "2023-08-28")]  # not the third expiry
    totals = {(r[0], r[2].isoformat(), r[3]): r[4] for r in got["totals"]}
    assert totals[("ABC", "2023-08-28", "CE")] == 700 and totals[("ABC", "2023-08-28", "PE")] == 900 and totals[("ABC", "2023-09-25", "CE")] == 100
    assert sorted((r[0], r[2], r[3]) for r in got["strikes"] if r[0] == "ABC") == [("ABC", 100.0, "CE"), ("ABC", 100.0, "PE")]  # no row for the strike with no open interest, none for the later expiry
    assert bhav.reduce(bhav.parse(OLD), DAY)["strikes"] == []
    assert bhav.reduce(bhav.parse(OLD), date(2023, 9, 1))["futures"][0][2] >= date(2023, 9, 1)  # an expiry already past is dropped


def test_urls_are_tried_in_the_order_of_the_layout_in_use():
    assert bhav.urls_for(date(2023, 8, 1))[0].endswith("DERIVATIVES/2023/AUG/fo01AUG2023bhav.csv.zip")
    assert bhav.urls_for(date(2024, 8, 1))[0].endswith("fo/BhavCopy_NSE_FO_0_0_0_20240801_F_0000.csv.zip")


def zipped(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("f.csv", text)
    return buffer.getvalue()


def unzip(content):
    return zipfile.ZipFile(io.BytesIO(content)).read("f.csv").decode()


def test_a_run_loads_weekdays_once_records_holidays_and_resumes_without_asking_again(tmp_path):
    asked = []

    def fetch(url):
        asked.append(url)
        return zipped(OLD) if "01AUG2023" in url else None  # only the 1st has a file

    with DerivsStore(tmp_path / "d.duckdb") as store:
        summary = ingest.run(store, date(2023, 8, 1), date(2023, 8, 10), fetch, unzip, out=io.StringIO())
        assert summary["loaded"] == 1 and summary["holidays"] == 3
        before = [u for u in asked if any(d in u for d in ("01AUG", "02AUG", "03AUG", "04AUG"))]
        again = ingest.run(store, date(2023, 8, 1), date(2023, 8, 10), fetch, unzip, out=io.StringIO())
        assert again["loaded"] == 0 and again["holidays"] == 0
        assert [u for u in asked if any(d in u for d in ("01AUG", "02AUG", "03AUG", "04AUG"))] == before  # the days already recorded are not asked for again
        cov = store.coverage()
        assert cov["days"] == 4 and cov["with_file"] == 1
        assert store.futures("ABC")[0] == (DAY, 101.0, 1300.0, 500.0)  # the nearest expiry close and volume, open interest over the two expiries held
        assert len(store.option_rows("ABC", DAY)) == 2 and store.symbols() == ["ABC"]


def test_two_refusals_in_a_row_stop_the_run_and_the_days_loaded_stay(tmp_path):
    def fetch(url):
        if "01AUG2023" in url:
            return zipped(OLD)
        raise AthenaError("refused")

    with DerivsStore(tmp_path / "d.duckdb") as store:
        summary = ingest.run(store, date(2023, 8, 1), date(2023, 8, 10), fetch, unzip, out=io.StringIO())
        assert summary["stopped"] == 1 and summary["loaded"] == 1 and store.coverage()["days"] == 1


def test_a_missing_file_in_the_last_days_is_not_recorded_as_a_holiday(tmp_path):
    with DerivsStore(tmp_path / "d.duckdb") as store:
        today = date(2023, 8, 2)
        summary = ingest.run(store, date(2023, 8, 1), today, lambda url: None, unzip, out=io.StringIO())
        assert summary["holidays"] == 0 and store.coverage()["days"] == 0


def test_the_scan_finds_a_build_up_on_the_latest_day_from_the_store(tmp_path):
    from datetime import timedelta

    from athena.flow import scan

    expiry = date(2030, 1, 30)
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(70)]
    with DerivsStore(tmp_path / "d.duckdb") as store:
        for i, day in enumerate(days):
            wobble = ((i * 7) % 5 - 2) / 1000
            close, oi = 100.0 * (1 + wobble), 1000.0 * (1 + wobble * 2)
            if i == 69:
                close, oi = 100.0 * 1.03, 1000.0 * 1.06
            store.put_day(day, {"futures": [("ABC", "FUTSTK", expiry, close, close, 10.0, oi, 0.0)], "totals": [], "strikes": []})
        events, held, ratio = scan.events_for(store, "ABC")
        assert held == 70 and ratio is None and [e.kind for e in events if e.kind != "oi_spike"] == ["long_build_up"]
        assert scan.scan(store)[0].symbol == "ABC" and scan.events_for(store, "NOPE") == ([], 0, None)


def _roll_rows():
    """A stock whose open interest drains from the near month into the next one over the expiry week, then the next month becomes the nearest. Price is flat, so nothing moved."""
    near, far, later = date(2026, 9, 29), date(2026, 10, 27), date(2026, 11, 24)
    rows = []
    for day, (a, b, c) in {
        date(2026, 9, 24): (80.0, 60.0, 0.0),
        date(2026, 9, 25): (60.0, 80.0, 0.0),
        date(2026, 9, 28): (30.0, 110.0, 0.0),
        date(2026, 9, 29): (10.0, 130.0, 0.0),
        date(2026, 9, 30): (0.0, 140.0, 15.0),
        date(2026, 10, 1): (0.0, 141.0, 16.0),
    }.items():
        if a:
            rows.append((day, near, 100.0, a, 1.0))
        rows.append((day, far, 101.0, b, 1.0))
        if c:
            rows.append((day, later, 102.0, c, 1.0))
    return rows


def test_a_roll_from_one_expiry_to_the_next_is_not_a_move():
    from athena.derivs.store import continuous

    rows = _roll_rows()
    series = continuous(rows)
    assert [s[0] for s in series] == sorted({r[0] for r in rows})
    closes = [s[1] for s in series]
    held = [s[2] for s in series]
    assert max(closes) / min(closes) == pytest.approx(1.0, abs=1e-9)  # a flat price stays flat across the roll
    assert held[-1] == pytest.approx(157.0)  # the last day total over the expiries held
    assert max(abs(b / a - 1) for a, b in zip(held, held[1:])) < 0.12
    nearest = {}
    for day, expiry, _, oi, _ in rows:
        if day not in nearest or expiry < nearest[day][0]:
            nearest[day] = (expiry, oi)
    old = [nearest[d][1] for d in sorted(nearest) if nearest[d][1] > 0]
    assert max(b / a for a, b in zip(old, old[1:])) > 10  # read the old way (the nearest expiry each day) the same rows jump by more than 1,000% on the roll day


def test_a_day_with_nothing_in_common_with_the_day_before_carries_no_change():
    from athena.derivs.store import continuous

    rows = [(date(2026, 1, 1), date(2026, 1, 29), 100.0, 50.0, 1.0), (date(2026, 1, 2), date(2026, 3, 26), 110.0, 70.0, 1.0)]
    series = continuous(rows)
    assert series[0][1] == pytest.approx(series[1][1]) == pytest.approx(110.0)
    assert series[0][2] == pytest.approx(series[1][2]) == pytest.approx(70.0)
    assert continuous([]) == []


def test_the_scan_reports_only_symbols_with_data_on_the_latest_day_of_the_store(tmp_path):
    from datetime import timedelta

    from athena.flow import scan

    expiry = date(2030, 1, 30)
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(70)]
    with DerivsStore(tmp_path / "d.duckdb") as store:
        for i, day in enumerate(days):
            wobble = ((i * 7) % 5 - 2) / 1000
            rows = []
            for symbol, last in (("ABC", 69), ("OLD", 68)):  # OLD has no row on the final day: it left the futures market
                if i > last:
                    continue
                close, oi = (100.0 * 1.03, 1000.0 * 1.06) if i == last else (100.0 * (1 + wobble), 1000.0 * (1 + wobble * 2))
                rows.append((symbol, "FUTSTK", expiry, close, close, 10.0, oi, 0.0))
            store.put_day(day, {"futures": rows, "totals": [], "strikes": []})
        assert scan.events_for(store, "OLD")[0]  # asked for by name, the stale symbol still answers
        assert {e.symbol for e in scan.scan(store)} == {"ABC"}
