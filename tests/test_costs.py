import json

import pytest

from athena.costs import model as m


def test_a_delivery_round_trip_pays_stt_stamp_exchange_sebi_gst_and_the_depository_charge_and_no_brokerage():
    c = m.load(path=__import__("pathlib").Path("does-not-exist.json"))
    buy, sell = m.leg(c, "delivery", "buy", 100000.0), m.leg(c, "delivery", "sell", 100000.0)
    assert buy["brokerage"] == 0 and buy["stt"] == pytest.approx(100.0) and buy["stamp"] == pytest.approx(15.0) and buy["dp"] == 0
    assert sell["stt"] == pytest.approx(100.0) and sell["stamp"] == 0 and sell["dp"] == pytest.approx(13.5 * 1.18)
    assert buy["gst"] == pytest.approx(0.18 * (buy["exchange"] + buy["sebi"]))
    assert buy["total"] == pytest.approx(sum(v for k, v in buy.items() if k != "total"))


def test_intraday_brokerage_is_a_fraction_of_the_order_capped_per_order_and_stt_is_on_the_sale_only():
    c = m.load(path=__import__("pathlib").Path("does-not-exist.json"))
    small, large = m.leg(c, "intraday", "buy", 10000.0), m.leg(c, "intraday", "buy", 1_000_000.0)
    assert small["brokerage"] == pytest.approx(3.0) and large["brokerage"] == 20.0
    assert m.leg(c, "intraday", "buy", 1e5)["stt"] == 0 and m.leg(c, "intraday", "sell", 1e5)["stt"] == pytest.approx(25.0)


def test_options_pay_a_flat_brokerage_and_stt_on_the_premium_sold():
    c = m.load(path=__import__("pathlib").Path("does-not-exist.json"))
    assert m.leg(c, "options", "buy", 5000.0)["brokerage"] == 20.0 and m.leg(c, "options", "sell", 100000.0)["stt"] == pytest.approx(100.0)


def test_slippage_depends_on_how_liquid_the_stock_is_and_is_left_out_when_that_is_unknown():
    c = m.load(path=__import__("pathlib").Path("does-not-exist.json"))
    assert m.slippage_per_side(c, 5) == 0.004 and m.slippage_per_side(c, 50) == 0.0015 and m.slippage_per_side(c, 5000) == 0.0003
    thin, liquid = m.round_trip(c, "delivery", 100000.0, 5), m.round_trip(c, "delivery", 100000.0, 5000)
    assert thin["slippage"] == pytest.approx(800.0) and thin["total"] > liquid["total"] and m.round_trip(c, "delivery", 100000.0)["slippage"] is None


def test_the_owners_file_overrides_only_what_it_names_and_sets_the_verified_flag(tmp_path):
    path = tmp_path / "charges.json"
    path.write_text(json.dumps({"verified": True, "as_of": "2026-11-01", "segments": {"delivery": {"stt_buy": 0.002}}}), encoding="utf-8")
    c = m.load(path)
    assert c["verified"] is True and c["segments"]["delivery"]["stt_buy"] == 0.002 and c["segments"]["delivery"]["stt_sell"] == 0.001 and c["segments"]["futures"]["stt_sell"] == 0.0002
    assert "NOT yet checked" not in m.format_round_trip(m.round_trip(c, "delivery", 1e5))
    assert "NOT yet checked" in m.format_round_trip(m.round_trip(m.load(tmp_path / "none.json"), "delivery", 1e5))
    path.write_text("not json", encoding="utf-8")
    assert m.load(path)["verified"] is False  # a broken file falls back to the defaults, flagged unchecked


def test_an_unknown_segment_or_side_is_an_error():
    c = m.load(path=__import__("pathlib").Path("does-not-exist.json"))
    with pytest.raises(ValueError):
        m.leg(c, "swaps", "buy", 1.0)
    with pytest.raises(ValueError):
        m.leg(c, "delivery", "hold", 1.0)
