"""What a trade costs in India: brokerage, taxes and fees, and slippage.

    python -m athena.costs.model delivery 100000        # the cost of buying and selling Rs 1 lakh of shares held for delivery

The rates are the owner's to confirm, not facts this code knows: they live in `.athena/charges.json` (dated, editable) and the defaults below are Zerodha's published schedule as the author
knew it, marked `verified: false` until the owner checks them against a contract note and sets it true. A result carries that flag. Every figure is in rupees; a rate is a fraction of the
traded value (of the premium for options). Segments: delivery (equity held overnight), intraday (equity bought and sold the same day), futures, options.

  brokerage   per order: a fraction of value, capped at a flat amount per order, or free
  stt         securities transaction tax, charged on the side(s) named
  exchange    the exchange's transaction charge, both sides
  sebi        SEBI turnover fee, both sides
  stamp       stamp duty, buy side only
  gst         on brokerage + exchange + sebi
  dp          depository charge for each sale from delivery, a flat amount (+ gst) per stock sold
  slippage    half the spread and the market's move against the order: a fraction of value, per side, by how liquid the stock is"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

CHARGES_PATH = Path(".athena") / "charges.json"
SEGMENTS = ("delivery", "intraday", "futures", "options")
DEFAULTS: dict[str, Any] = {
    "as_of": "2026-10-24",
    "source": "Zerodha's published schedule as the author knew it; not checked against a contract note",
    "verified": False,
    "gst": 0.18,
    "sebi": 0.000001,  # Rs 10 per crore
    "dp_flat": 13.5,  # per stock sold from delivery, before gst
    "segments": {
        "delivery": {"brokerage_rate": 0.0, "brokerage_cap": 0.0, "stt_buy": 0.001, "stt_sell": 0.001, "exchange": 0.0000297, "stamp_buy": 0.00015},
        "intraday": {"brokerage_rate": 0.0003, "brokerage_cap": 20.0, "stt_buy": 0.0, "stt_sell": 0.00025, "exchange": 0.0000297, "stamp_buy": 0.00003},
        "futures": {"brokerage_rate": 0.0003, "brokerage_cap": 20.0, "stt_buy": 0.0, "stt_sell": 0.0002, "exchange": 0.0000183, "stamp_buy": 0.00002},
        "options": {"brokerage_rate": 0.0, "brokerage_cap": 20.0, "flat_brokerage": 20.0, "stt_buy": 0.0, "stt_sell": 0.001, "exchange": 0.0003503, "stamp_buy": 0.00003},
    },
    # slippage per side as a fraction of value, by the stock's average daily traded value in rupees crore (the first bucket whose ceiling is not below the value)
    "slippage": [{"up_to_crore": 10, "per_side": 0.004}, {"up_to_crore": 100, "per_side": 0.0015}, {"up_to_crore": 1000, "per_side": 0.0006}, {"up_to_crore": 1e12, "per_side": 0.0003}],
}


def load(path: Path = CHARGES_PATH) -> dict[str, Any]:
    """The owner's file when there is one (anything it leaves out comes from the defaults), else the defaults."""
    try:
        mine = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULTS))
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update({k: v for k, v in mine.items() if k != "segments"})
    for name, rates in (mine.get("segments") or {}).items():
        merged["segments"].setdefault(name, {}).update(rates)
    return merged


def leg(charges: Mapping[str, Any], segment: str, side: str, value: float) -> dict[str, float]:
    """The charges on one order of `value` rupees: `side` is "buy" or "sell". Slippage is separate (`slippage_per_side`)."""
    if segment not in SEGMENTS or side not in ("buy", "sell"):
        raise ValueError(f"unknown segment {segment!r} or side {side!r}")
    r = charges["segments"][segment]
    flat = r.get("flat_brokerage")
    brokerage = float(flat) if flat else min(value * r["brokerage_rate"], r["brokerage_cap"]) if r["brokerage_cap"] else value * r["brokerage_rate"]
    exchange, sebi = value * r["exchange"], value * charges["sebi"]
    parts = {
        "brokerage": brokerage,
        "stt": value * (r["stt_buy"] if side == "buy" else r["stt_sell"]),
        "exchange": exchange,
        "sebi": sebi,
        "stamp": value * r["stamp_buy"] if side == "buy" else 0.0,
        "gst": charges["gst"] * (brokerage + exchange + sebi),
        "dp": charges["dp_flat"] * (1 + charges["gst"]) if segment == "delivery" and side == "sell" else 0.0,
    }
    parts["total"] = sum(parts.values())
    return parts


def slippage_per_side(charges: Mapping[str, Any], daily_traded_crore: float) -> float:
    for bucket in charges["slippage"]:
        if daily_traded_crore <= bucket["up_to_crore"]:
            return float(bucket["per_side"])
    return float(charges["slippage"][-1]["per_side"])


def round_trip(charges: Mapping[str, Any], segment: str, value: float, daily_traded_crore: float | None = None) -> dict[str, Any]:
    """Buying then selling `value` rupees (the sale taken at the same value): the charges on each leg, slippage on both when the stock's traded value is known, the total and it as a fraction of
    the value. `verified` is the file's flag, passed on so no one reads an unchecked number as checked."""
    buy, sell = leg(charges, segment, "buy", value), leg(charges, segment, "sell", value)
    slip = 2 * value * slippage_per_side(charges, daily_traded_crore) if daily_traded_crore is not None else None
    total = buy["total"] + sell["total"] + (slip or 0.0)
    return {"segment": segment, "value": value, "buy": buy, "sell": sell, "slippage": slip, "total": total, "fraction": total / value if value else 0.0, "verified": bool(charges["verified"]), "as_of": charges["as_of"]}


def format_round_trip(rt: Mapping[str, Any]) -> str:
    note = "" if rt["verified"] else " (rates NOT yet checked against a contract note)"
    slip = "slippage not included" if rt["slippage"] is None else f"slippage Rs {rt['slippage']:,.0f}"
    return (f"{rt['segment']} round trip on Rs {rt['value']:,.0f}: charges Rs {rt['buy']['total'] + rt['sell']['total']:,.2f}, {slip}; "
            f"total Rs {rt['total']:,.2f} = {rt['fraction']:.3%} of the value{note}")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) < 2 or args[0] not in SEGMENTS:
        print(f"usage: python -m athena.costs.model {{{'|'.join(SEGMENTS)}}} VALUE_IN_RUPEES [DAILY_TRADED_CRORE]")
        return 2
    crore = float(args[2]) if len(args) > 2 else None
    print(format_round_trip(round_trip(load(), args[0], float(args[1]), crore)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
