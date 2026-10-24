"""The valuation model's results as figures the valuation analyst may cite.

The model (`scenarios`, `dcf`, `excess_return`) is code; the analyst only reads what it gives. This module turns the page document (`page.build_valuation`) into entries of the
metrics packet, each with its unit, window and source, and adds them to the fundamentals packet. A figure the model could not give is listed as missing with the reason, never
filled in. The model's value runs below the market's price for most companies (the calibration study, `docs/valuation-calibration-2026-10-22.md`), so the packet also holds the
market's own median price to base value from the latest ledger snapshot and this company's ratio against it: a ratio of 2 means little when the whole market sits near 2."""
from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from athena.valuation.ledger import DEFAULT_PATH as LEDGER, Ledger

VALUATION = "valuation"
MIN_MARKET_ROWS = 50  # fewer companies than this and the market's median says nothing
SENSITIVITY_NAMES = (
    ("Equity premium = mature", "model_base_change_mature_premium_only", "the model's base value if the India country premium is left out of the equity premium"),
    ("Rupee inflation", "model_base_change_inflation_up", "the model's base value if rupee inflation is one point higher"),
    ("No rupee conversion", "model_base_change_no_conversion", "the model's base value if dollar rates are used as rupee rates"),
)
NAMES = (
    "model_value_low", "model_value_base", "model_value_high", "price_to_model_base", "price_to_model_base_vs_market", "market_price_to_model_base_median",
    "implied_return", "required_return", "implied_minus_required", *(name for _, name, _ in SENSITIVITY_NAMES),
)


def market_gap(rows: Sequence[Mapping[str, Any]]) -> float | None:
    """The median of price over base value across the companies in a ledger snapshot (rows with a positive base), or None when there are too few."""
    ratios = [float(r["price"]) / float(r["base"]) for r in rows if r.get("price") and r.get("base") and float(r["base"]) > 0]
    return statistics.median(ratios) if len(ratios) >= MIN_MARKET_ROWS else None


def _entry(value: float, unit: str, inputs: list[str], window: str, source: str, note: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"value": round(float(value), 4), "unit": unit, "inputs": inputs, "window": window, "source": source, "group": VALUATION}
    if note:
        entry["note"] = note
    return entry


def model_metrics(doc: Mapping[str, Any], gap: float | None = None, gap_date: date | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """`(metrics, reasons)`: the entries the model's document gives, and why any other name in `NAMES` is not available."""
    metrics: dict[str, dict[str, Any]] = {}
    if doc.get("error") or not doc.get("ok"):
        why = str(doc.get("error") or "; ".join(doc.get("flags") or doc.get("missing") or ["the model could not value the company"]))[:240]
        return metrics, {name: f"the valuation model gave no value: {why}" for name in NAMES}
    on = str(doc.get("on_date"))
    source = f"Athena valuation model ({doc.get('model')}; industry {doc.get('industry')}), computed in code, rupees per share"
    for key, name in (("low", "model_value_low"), ("base", "model_value_base"), ("high", "model_value_high")):
        if doc.get(key) is not None:
            metrics[name] = _entry(doc[key], "INR/share", [key], on, source, "bear, base and bull cases from written rules; a range, not a target price")
    price, base = doc.get("price"), doc.get("base")
    reasons: dict[str, str] = {}
    if price and base and base > 0:
        ratio = price / base
        metrics["price_to_model_base"] = _entry(ratio, "ratio", ["close", "model_value_base"], on, source, "above 1: the price is above the model's base value")
        if gap:
            metrics["market_price_to_model_base_median"] = _entry(gap, "ratio", ["ledger snapshot"], str(gap_date), "valuation ledger", "the median of this ratio over every company valued that day")
            metrics["price_to_model_base_vs_market"] = _entry(ratio / gap, "ratio", ["price_to_model_base", "market_price_to_model_base_median"], on, source, "above 1: dearer against the model than the typical company")
        else:
            reasons["price_to_model_base_vs_market"] = reasons["market_price_to_model_base_median"] = "no ledger snapshot with enough companies"
    else:
        reasons["price_to_model_base"] = reasons["price_to_model_base_vs_market"] = "the model's base value is not positive or there is no price"
    if doc.get("implied_return") is not None:
        metrics["implied_return"] = _entry(doc["implied_return"], "fraction", ["close", "model cash flows"], on, source, "the yearly return that makes the model's base cash flows worth today's price")
    if doc.get("required_return") is not None:
        metrics["required_return"] = _entry(doc["required_return"], "fraction", ["cost of capital"], on, source, "the model's base-case cost of capital (cost of equity for a lender)")
    if "implied_return" in metrics and "required_return" in metrics:
        gap_value = metrics["implied_return"]["value"] - metrics["required_return"]["value"]
        metrics["implied_minus_required"] = _entry(gap_value, "fraction", ["implied_return", "required_return"], on, source, "above 0: the price pays more than the model's required return")
    for item in doc.get("sensitivity") or []:
        for prefix, name, text in SENSITIVITY_NAMES:
            if str(item.get("label", "")).startswith(prefix) and item.get("change") is not None:
                metrics[name] = _entry(item["change"], "fraction", ["model_value_base"], on, source, text)
    for name in NAMES:
        if name not in metrics and name not in reasons:
            reasons[name] = "the model did not give it for this company"
    return metrics, reasons


def with_model(packet: Mapping[str, Any], doc: Mapping[str, Any] | None, gap: float | None = None, gap_date: date | None = None) -> dict[str, Any]:
    """The fundamentals packet with the model's figures added (a new dict). With no document the packet is returned as it is and the names are listed missing."""
    out = {**packet, "metrics": dict(packet.get("metrics", {})), "missing": list(packet.get("missing", [])), "missing_reasons": dict(packet.get("missing_reasons", {})),
           "groups": dict(packet.get("groups", {}))}
    metrics, reasons = model_metrics(doc if doc is not None else {"error": "the model was not run"}, gap, gap_date)
    out["metrics"].update(metrics)
    for name, why in reasons.items():
        out["missing"].append(name)
        out["missing_reasons"][name] = why
    for name in NAMES:
        out["groups"][name] = VALUATION
    if doc is not None and doc.get("ok"):
        out["model_cautions"] = list(doc.get("flags") or [])[:6]
        out["model_assumptions"] = list(doc.get("assumptions") or [])[:6]
        out["model_business_mix"] = list(doc.get("business_mix") or [])
    return out


def live_facts(
    fundamentals_path: str | Path | None = None, reference_path: str | Path | None = None, ledger_path: str | Path = LEDGER, today: Callable[[], date] = date.today
) -> Callable[[str, float | None], tuple[Mapping[str, Any] | None, float | None, date | None]]:
    """A `(symbol, price) -> (document, market gap, gap date)` function over the stores on disk. A store that is busy or missing gives `(None, None, None)` for that call."""
    from athena.fundamentals.store import DEFAULT_PATH as FUNDAMENTALS, open_read
    from athena.reference.store import DEFAULT_PATH as REFERENCE, ReferenceStore
    from athena.valuation.page import build_valuation

    fund_path, ref_path = fundamentals_path or FUNDAMENTALS, reference_path or REFERENCE
    cache: dict[str, Any] = {}

    def market() -> tuple[float | None, date | None]:
        if "gap" not in cache:
            try:
                with Ledger(ledger_path, read_only=True) as book:
                    dates = book.snapshot_dates()
                    cache["gap"] = (market_gap(book.rows(dates[-1])), dates[-1]) if dates else (None, None)
            except Exception:
                cache["gap"] = (None, None)
        return cache["gap"]

    def find(symbol: str, price: float | None) -> tuple[Mapping[str, Any] | None, float | None, date | None]:
        try:
            with open_read(fund_path) as fund, ReferenceStore(ref_path, read_only=True) as ref:
                doc = build_valuation(fund, ref, symbol, "consolidated", price, "last close of the market database", today())
        except Exception:
            return None, None, None
        gap, gap_date = market()
        return doc, gap, gap_date

    return find
