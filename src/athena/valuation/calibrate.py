"""A calibration study for the valuation rules: the same model run under alternative inputs and rules ("variants"), scored on the whole stored universe at past dates.

Two things are measured, because they answer different questions.

  level   where the model's base value sits against the price across the universe (median price / base, how often the price is inside the range, banks against book).
          There is no true value to compare with, so the level is read as a bias check, never as a target to fit.
  ranking at each past date the model is run on what had been filed by then (statement lines carry their filing time) with the price of that day, and the rank correlation
          (Spearman, the "information coefficient") of ln(base / price) with the price return over the next six months is taken, with the top-minus-bottom quintile
          spread. The same is done for plain earnings yield and book-to-price, so the model has to beat a one-line value factor to justify its complexity.

Known limits, stated on every report: prices are raw closes, so returns are adjusted for the splits and bonuses the exchange lists and a company with a demerger, scheme or
capital reduction after the date is left out; dividends are not added to returns; a company that is no longer listed has no later price and is absent (survivorship);
with the approximate `latest_filtered` statement policy a restated figure replaces the original, so a period restated after the date drops out instead of being read as first filed (the strict `vintage`
policy reads the filing that was public then, from `statement_versions`, and a period whose original was never stored is absent; `Snapshot.statement_policy` says which ran); the industry tables are the 2026 ones
for every date; and the market-wide inputs (risk-free rate, equity premium) are today's for every date, which shifts levels but hardly the ranking inside one date."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import AthenaError
from athena.evaluation.policy import APPROXIMATE_CURRENT, INPUT_POLICY_LABELS, POLICY, STRICT_DATED
from athena.fundamentals import history
from athena.fundamentals import shares as share_counts
from athena.fundamentals.store import FundamentalsStore
from athena.price_integrity.actions import parse_subject
from athena.reference.store import ReferenceStore
from athena.valuation import inputs, scenarios, wacc
from athena.valuation import industry as ind
from athena.valuation.manifest import input_manifest
from athena.valuation.industry import IndustryContext
from athena.valuation.scenarios import Policy

PRICE_WINDOW_DAYS = 10  # a close older than this before the wanted day is not "the price on that day"
SHARE_CHANGING = {"bonus", "split"}
UNADJUSTABLE = {"demerger", "scheme", "capital_reduction"}
MIN_MARKET_CAP_CRORE = 500.0
LARGE_MARKET_CAP_CRORE = 5000.0
MIN_GROUP = 8  # an industry needs this many names for an industry-relative signal


@dataclass(frozen=True)
class Variant:
    """One set of alternatives to the defaults. Every field left at None / default is the live rule."""

    name: str
    note: str = ""
    policy: Policy = field(default_factory=Policy)
    erp_crp_share: float | None = None  # total equity premium = mature + share x the country premium
    erp_total: float | None = None  # or set outright
    inflation_inr: float | None = None
    inflation_usd: float | None = None
    beta_shrink: float = 0.0  # industry betas move this share of the way toward the cross-industry average (unlevered) or 1 (levered)


# ---- overrides: a store that reports a variant's inputs in place of the dated ones

class OverrideStore:
    """Wraps a reference store and reports a variant's alternative inputs. Everything else passes through."""

    def __init__(self, base: ReferenceStore, variant: Variant):
        self._base = base
        self._variant = variant
        self._inputs: dict[tuple[str, date], Any] = {}
        self._premiums: dict[tuple[str, date | None], Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def input(self, key: str, on_or_before: date):
        if (key, on_or_before) not in self._inputs:
            self._inputs[(key, on_or_before)] = self._input(key, on_or_before)
        return self._inputs[(key, on_or_before)]

    def _input(self, key: str, on_or_before: date):
        got = self._base.input(key, on_or_before)
        override = {"inflation_inr": self._variant.inflation_inr, "inflation_usd": self._variant.inflation_usd}.get(key)
        if got is not None and override is not None:
            return replace(got, value=override)
        return got

    def country_premium(self, country: str, on_or_before: date | None = None):
        if (country, on_or_before) not in self._premiums:
            self._premiums[(country, on_or_before)] = self._country_premium(country, on_or_before)
        return self._premiums[(country, on_or_before)]

    def _country_premium(self, country: str, on_or_before: date | None):
        row = self._base.country_premium(country, on_or_before)
        v = self._variant
        if row is None or country != "India" or (v.erp_total is None and v.erp_crp_share is None):
            return row
        rating, spread, total, crp, as_of = row
        if v.erp_total is not None:
            total = v.erp_total
        else:
            mature = self._base.input("erp_mature", on_or_before)
            if mature is not None:
                total = mature.value + v.erp_crp_share * crp  # type: ignore[operator]
        return (rating, spread, total, crp, as_of)


def shrink_betas(context: IndustryContext, average_unlevered: float, share: float) -> IndustryContext:
    """The industry betas pulled `share` of the way toward the cross-industry average (unlevered beta) or toward 1 (the raw levered beta): Blume's adjustment for estimation error."""
    if share <= 0:
        return context
    values = dict(context.values)
    if "unlevered_beta" in values:
        values["unlevered_beta"] = (1 - share) * values["unlevered_beta"] + share * average_unlevered
    if "beta" in values:
        values["beta"] = (1 - share) * values["beta"] + share * 1.0
    return replace(context, values=values)


# ---- the past: what was filed by a day, split-adjusted

VINTAGE, LATEST_FILTERED = "vintage", "latest_filtered"
POLICY_LABELS = {
    VINTAGE: "statements read from the version of each filing that was public at the date (statement_versions); a period whose original is not held is absent, not filled from a later restatement",
    LATEST_FILTERED: "approximate statements: today's latest-value table filtered by filing date, so a period restated after the date drops out instead of being read as first filed",
}


class AsOfFundamentals:
    """The fundamentals store as it stood at a moment: the statements public by then, and the share count that applied then.

    `policy` says how the statements are read. `"vintage"` is the strict reader: for each period the version (filing) that was public at the cutoff, from the immutable `statement_versions`
    (`FundamentalsStore.lines_as_of`); it raises when the store holds no versions, because an empty answer would look like "no company had statements". `"latest_filtered"` is the old approximation,
    kept and LABELLED: the latest-value table filtered by `filed <= cutoff`, which cannot show an original that a revision replaced. `"auto"` (the default) is `vintage` when the store has versions and
    `latest_filtered` when it has none; `policy` and `label` say which was used, and a report must carry the label."""

    def __init__(
        self, base: FundamentalsStore, cutoff: datetime, share_factor: dict[str, float], policy: str = "auto", *, inputs: str = APPROXIMATE_CURRENT,
        actions: dict[str, list[tuple[date, str, float | None]]] | None = None, day: date | None = None,
    ):
        """`inputs` is the study's input policy (`athena.evaluation.policy`). `approximate_current` (the default, and what every earlier study was) takes today's share count and divides it by the splits
        after the date (`share_factor`). `strict_dated` takes the share count from the latest filing by the date and adjusts it for the splits and bonuses between that filing's period end and the date
        (`actions`, `day` are then required); a company with no such count has `shares` None, and `share_status` says why. It never falls back to today's count."""
        if policy not in ("auto", VINTAGE, LATEST_FILTERED):
            raise ValueError(f"unknown statement policy {policy!r}")
        if inputs not in (STRICT_DATED, APPROXIMATE_CURRENT):
            raise ValueError(f"unknown input policy {inputs!r}")
        if inputs == STRICT_DATED and (actions is None or day is None):
            raise ValueError("a strict dated study needs the corporate actions and the study date to adjust a dated share count")
        self._base = base
        self._cutoff = cutoff
        self._factor = share_factor
        self.inputs = inputs
        self._actions, self._day = actions or {}, day
        self.share_versions: dict[str, dict[str, Any]] = {}
        self._share_status: dict[str, str] = {}
        self.policy = (VINTAGE if base.has_versions() else LATEST_FILTERED) if policy == "auto" else policy
        self.label = POLICY_LABELS[self.policy]

    def _dated_shares(self, symbol: str) -> float | None:
        counts = self._base.share_counts(symbol)
        choice = share_counts.choose_as_of(counts, self._cutoff)
        if choice.shares is None or choice.period_end is None:
            self._share_status[symbol] = f"no share count had been filed by {self._day.isoformat()}"  # type: ignore[union-attr]
            return None
        factor = adjustment(self._actions.get(symbol, ()), choice.period_end, self._day)  # type: ignore[arg-type]
        if factor is None:
            self._share_status[symbol] = (
                f"a split, bonus or restructuring after {choice.period_end} cannot be adjusted for (an unreadable ratio, or a demerger, scheme or capital reduction): "
                f"the share count on {self._day.isoformat()} is not known"  # type: ignore[union-attr]
            )
            return None
        source = next((c for c in counts if c.get("period_end") == choice.period_end and c.get("declared") == choice.shares), None) or {}
        self._share_status[symbol] = ""
        self.share_versions[symbol] = {
            "period_end": choice.period_end, "filed": source.get("filed"), "url": source.get("url"), "how": choice.how, "declared": choice.shares, "adjustment": factor, "shares": choice.shares * factor,
        }
        return choice.shares * factor

    def share_status(self, symbol: str) -> str:
        """For a strict study: why the company's dated share count is missing, or "" when it has one."""
        if symbol not in self._share_status and self.inputs == STRICT_DATED:
            self._dated_shares(symbol)
        return self._share_status.get(symbol, "")

    def lines(self, symbol: str, basis: str, statement: str) -> dict[tuple[date, date], dict[str, float]]:
        if self.policy == VINTAGE:
            if not self._base.has_versions():
                raise AthenaError("this store has no statement versions, so a strict as-of read is not possible: run python -m athena.fundamentals.vintages on a copy, or use policy='latest_filtered' (approximate)")
            return self._base.lines_as_of(symbol, basis, statement, self._cutoff)
        out: dict[tuple[date, date], dict[str, float]] = {}
        rows = self._base._con.execute(
            "SELECT period_start, period_end, key, value FROM statement_lines WHERE symbol = ? AND basis = ? AND statement = ? AND filed <= ? ORDER BY period_end, period_start",
            [symbol, basis, statement, self._cutoff],
        ).fetchall()
        for start, end, key, value in rows:
            out.setdefault((start, end), {})[key] = value
        return out

    def company(self, symbol: str) -> dict[str, Any] | None:
        row = self._base.company(symbol)
        if row is None:
            return None
        if self.inputs == STRICT_DATED:
            if symbol not in self._share_status:
                self._dated_shares(symbol)
            return {**row, "shares": self.share_versions[symbol]["shares"] if symbol in self.share_versions else None}
        shares = row.get("shares")
        factor = self._factor.get(symbol, 1.0)
        return {**row, "shares": shares / factor if shares else shares}


def load_actions(warehouse: Path) -> dict[str, list[tuple[date, str, float | None]]]:
    """Every share-count-relevant corporate action by symbol: (ex-date, kind, ratio). Dividends and meetings are not returned."""
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = con.execute("SELECT symbol, ex_date, subject FROM actions").fetchall()
    finally:
        con.close()
    out: dict[str, list[tuple[date, str, float | None]]] = defaultdict(list)
    for symbol, ex_date, subject in rows:
        action = parse_subject(ex_date, subject)
        if action is not None and (action.kind in SHARE_CHANGING or action.kind in UNADJUSTABLE):
            out[symbol].append((ex_date, action.kind, action.ratio))
    return dict(out)


def adjustment(actions: Sequence[tuple[date, str, float | None]], after: date, until: date | None = None) -> float | None:
    """The product of the split and bonus ratios with ex-dates in (after, until]; None when one of them cannot be adjusted for (no ratio, or a demerger, scheme or capital reduction)."""
    factor = 1.0
    for ex_date, kind, ratio in actions:
        if ex_date <= after or (until is not None and ex_date > until):
            continue
        if kind in UNADJUSTABLE or ratio is None or ratio <= 0:
            return None
        factor *= ratio
    return factor


def closes_on(warehouse: Path, day: date) -> dict[str, float]:
    """The last close on or before `day` (within PRICE_WINDOW_DAYS) for every symbol."""
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = con.execute(
            "SELECT symbol, arg_max(close, day) FROM bars WHERE day <= ? AND day > ? AND close IS NOT NULL GROUP BY symbol", [day, day - timedelta(days=PRICE_WINDOW_DAYS)]
        ).fetchall()
    finally:
        con.close()
    return {s: c for s, c in rows}


@dataclass(frozen=True)
class Row:
    symbol: str
    company: inputs.CompanyInputs
    industry: IndustryContext | None
    forward: dict[int, float]  # months ahead -> split-adjusted price return
    outcome: dict[int, str] = field(default_factory=dict)  # months ahead -> "priced", "restructured" (return unknown from the price alone), "no_end_price" (stopped trading: proceeds unknown) or "not_yet_known"; strict studies only


@dataclass(frozen=True)
class Snapshot:
    day: date
    rows: tuple[Row, ...]
    excluded: dict[str, int]  # reason -> count
    statement_policy: str = ""  # which `AsOfFundamentals` reader supplied the statements ("vintage" or the approximate "latest_filtered"): a report states it
    inputs: str = ""  # the input policy the snapshot ran under (`athena.evaluation.policy`): "strict_dated" or "approximate_current"
    missing: dict[str, list[str]] = field(default_factory=dict)  # strict studies: a required dated input that did not exist -> the companies that abstained for it (they stay accounted for)
    manifest: dict[str, Any] = field(default_factory=dict)  # the exact input versions read (`athena.valuation.manifest`) and the share-count summary


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = (date(year + (month // 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def build_snapshot(
    fund: FundamentalsStore, ref: ReferenceStore, warehouse: Path, day: date, today: date, horizons: Sequence[int] = (6, 12), actions: dict[str, list[tuple[date, str, float | None]]] | None = None,
    symbols: Sequence[str] | None = None, progress: Callable[[str], None] | None = None, statement_policy: str = "auto", input_policy: str = APPROXIMATE_CURRENT,
) -> Snapshot:
    """Every stored company as it stood on `day`: its inputs from what had been filed, the price of the day, and its later returns where the later prices exist. `statement_policy` is
    `AsOfFundamentals`'s: `vintage` (strict), `latest_filtered` (approximate) or `auto`; the snapshot records which was used.

    `input_policy` is the study's (`athena.evaluation.policy`, locked in docs/audit-2026-11-12/WP04-STUDY-POLICY.md). `approximate_current` is the earlier behaviour, kept and labelled: today's share
    count, industry map and industry tables read for the past date, and a company with a demerger, scheme or capital reduction AFTER the date is left out. `strict_dated` reads every input as it stood
    on the date; a company missing one abstains and is listed in `Snapshot.missing` under the input it lacked; nothing that happens after the date decides who is in the snapshot (a later demerger
    leaves the company in, and its outcome is `restructured`); a company that stopped trading is in with outcome `no_end_price`. Every company is accounted for: rows + excluded + missing = symbols."""
    strict = input_policy == STRICT_DATED
    if input_policy not in (STRICT_DATED, APPROXIMATE_CURRENT):
        raise ValueError(f"unknown input policy {input_policy!r}")
    actions = load_actions(warehouse) if actions is None else actions
    price = closes_on(warehouse, day)
    later = {h: closes_on(warehouse, add_months(day, h)) for h in horizons}
    last_bar_day = _last_bar_day(warehouse)
    if symbols is None:
        symbols = [r[0] for r in fund._con.execute("SELECT symbol FROM companies ORDER BY symbol").fetchall()]
    factors: dict[str, float] = {}
    excluded: dict[str, int] = defaultdict(int)
    missing: dict[str, list[str]] = defaultdict(list)
    keep: list[str] = []
    for symbol in symbols:
        if symbol not in price:
            excluded["no price on the day"] += 1
            continue
        if not strict:
            factor = adjustment(actions.get(symbol, ()), day)
            if factor is None:
                excluded["a demerger, scheme, capital reduction or unreadable split after the day"] += 1
                continue
            factors[symbol] = factor
        keep.append(symbol)
    as_of = AsOfFundamentals(
        fund, datetime.combine(day, datetime.max.time()), factors, statement_policy, inputs=input_policy, actions=actions if strict else None, day=day if strict else None,
    )
    rows: list[Row] = []
    for n, symbol in enumerate(keep):
        if strict:
            why = as_of.share_status(symbol) if fund.company(symbol) is not None else ""
            if why:
                missing["share count: " + ("none filed by the day" if why.startswith("no share count") else "a corporate action since the filing cannot be adjusted for")].append(symbol)
                continue
        company = inputs.load_company_inputs(as_of, symbol, "consolidated", price[symbol])  # type: ignore[arg-type]
        if company is None:
            company = inputs.load_company_inputs(as_of, symbol, "standalone", price[symbol])  # type: ignore[arg-type]
        if company is None:
            excluded["no statements filed by the day"] += 1
            continue
        if strict:
            context, reason = ind.lookup_as_of(ref, symbol, day)
            if context is None:
                missing["industry: " + ("no company mapping dated by the day" if "mapping" in reason else "no industry tables published by the day")].append(symbol)
                continue
        else:
            context = ind.lookup(ref, symbol, today)
        forward: dict[int, float] = {}
        outcome: dict[int, str] = {}
        for h in horizons:
            end = add_months(day, h)
            if strict:
                if end > last_bar_day:
                    outcome[h] = "not_yet_known"
                    continue
                window = adjustment(actions.get(symbol, ()), day, end)
                if window is None:
                    outcome[h] = "restructured"  # the return cannot be read from the price alone: unknown, and the company stays in
                    continue
                if symbol not in later[h]:
                    outcome[h] = "no_end_price"  # stopped trading before the end of the window: proceeds unknown
                    continue
                forward[h] = later[h][symbol] * window / price[symbol] - 1.0
                outcome[h] = "priced"
                continue
            if end > last_bar_day or symbol not in later[h]:
                continue
            window = adjustment(actions.get(symbol, ()), day, end)
            if window is None:
                continue
            forward[h] = later[h][symbol] * window / price[symbol] - 1.0
        rows.append(Row(symbol, company, context, forward, outcome))
        if progress and n % 500 == 0:
            progress(f"{day}: {n}/{len(keep)}")
    version_note = {
        "share_counts": {"dated": len(as_of.share_versions), "missing": sum(len(v) for k, v in missing.items() if k.startswith("share count"))},
        "statements": as_of.label, "outcomes": {h: dict(Counter(r.outcome.get(h, "") for r in rows)) for h in horizons} if strict else {},
    }
    manifest_ = {**input_manifest(ref, day, policy=input_policy, today=today), **version_note}
    return Snapshot(day, tuple(rows), dict(excluded), as_of.policy, input_policy, {k: sorted(v) for k, v in missing.items()}, manifest_)


def _last_bar_day(warehouse: Path) -> date:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return con.execute("SELECT max(day) FROM bars").fetchone()[0]
    finally:
        con.close()


# ---- running a variant

@dataclass(frozen=True)
class Out:
    symbol: str
    model: str
    price: float
    low: float
    base: float
    high: float
    justified_price_to_book: float | None
    forward: dict[int, float]
    market_cap: float | None
    industry: str | None
    earnings_yield: float | None
    book_to_price: float | None
    outcome: dict[int, str] = field(default_factory=dict)  # months ahead -> how the later return was (or was not) measured; see `Row.outcome`


def average_unlevered_beta(rows: Sequence[Row]) -> float:
    seen: dict[str, float] = {}
    for r in rows:
        if r.industry is not None and r.industry.get("unlevered_beta") is not None:
            seen[r.industry.industry] = r.industry.get("unlevered_beta")  # type: ignore[assignment]
    return sum(seen.values()) / len(seen) if seen else 1.0


def run_variant(
    snapshot: Snapshot, variant: Variant, ref: ReferenceStore, today: date, on_date: date | None = None, abstained: dict[str, tuple[str, ...]] | None = None,
) -> list[Out]:
    """Value every row of the snapshot under the variant. The rates and tables are read as of `on_date` (a strict study passes the snapshot's own date; the default, `today`, is the approximate
    study's, which reads today's inputs for a past date). A company whose valuation could not be made because an input does not exist is NOT silently dropped: with `abstained` given it is listed
    there with what was missing."""
    store = OverrideStore(ref, variant)
    average = average_unlevered_beta(snapshot.rows)
    policy = replace(variant.policy, detail=False)
    read = on_date or today
    outs: list[Out] = []
    for row in snapshot.rows:
        context = shrink_betas(row.industry, average, variant.beta_shrink) if row.industry is not None else None
        capital = wacc.company_cost_of_capital(store, row.company, context, read)  # type: ignore[arg-type]
        got = scenarios.value_company(store, row.company, context, capital, read, policy)  # type: ignore[arg-type]
        if not got.ok:
            if abstained is not None:
                abstained[row.symbol] = tuple(got.missing) or tuple(getattr(capital, "missing", ()))
            continue
        if got.base is None or got.low is None or got.high is None or got.base <= 0 or not row.company.price:
            continue
        c = row.company
        outs.append(Out(
            row.symbol, got.model, row.company.price, got.low, got.base, got.high, got.justified_price_to_book, row.forward, c.market_cap,
            row.industry.industry if row.industry else None,
            c.net_profit / c.market_cap if c.net_profit is not None and c.market_cap else None,
            c.book_equity / c.market_cap if c.book_equity is not None and c.market_cap else None,
            row.outcome,
        ))
    return outs


# ---- scoring

def ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 10:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sxx * syy)


def quintile_returns(signal: Sequence[float], forward: Sequence[float]) -> list[float] | None:
    """The mean return of each fifth of the names by signal, lowest signal first."""
    n = len(signal)
    if n < 25:
        return None
    order = sorted(range(n), key=lambda i: signal[i])
    size = n // 5
    out = []
    for q in range(5):
        chunk = order[q * size : (q + 1) * size if q < 4 else n]
        out.append(sum(forward[i] for i in chunk) / len(chunk))
    return out


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def level_metrics(outs: Sequence[Out]) -> dict[str, Any]:
    ratios_ = [o.price / o.base for o in outs]
    fcff = [o.price / o.base for o in outs if o.model == "fcff"]
    lenders = [o.price / o.base for o in outs if o.model == "excess_return"]
    inside = sum(1 for o in outs if o.low <= o.price <= o.high)
    above = sum(1 for o in outs if o.price > o.high)
    pb = [o.justified_price_to_book for o in outs if o.model == "excess_return" and o.justified_price_to_book is not None]
    return {
        "n": len(outs), "n_fcff": len(fcff), "n_lender": len(lenders),
        "median_price_to_base": median(ratios_), "p25": quantile(ratios_, 0.25), "p75": quantile(ratios_, 0.75),
        "median_fcff": median(fcff), "median_lender": median(lenders),
        "inside_range": inside / len(outs) if outs else None, "above_range": above / len(outs) if outs else None,
        "lender_justified_pb_median": median(pb),
        "lender_below_book": sum(1 for v in pb if v < 1.0) / len(pb) if pb else None,
    }


def _signals(outs: Sequence[Out], horizon: int, min_cap: float) -> dict[str, tuple[list[float], list[float]]]:
    """For each signal, (signal values, forward returns) over the names that have the signal, a later price and the minimum size."""
    pool = [o for o in outs if horizon in o.forward and o.market_cap is not None and o.market_cap >= min_cap]
    out: dict[str, tuple[list[float], list[float]]] = {}
    out["model: ln(base/price)"] = ([math.log(o.base / o.price) for o in pool], [o.forward[horizon] for o in pool])
    out["model: ln(high/price)"] = ([math.log(o.high / o.price) for o in pool], [o.forward[horizon] for o in pool])
    groups: dict[str, list[Out]] = defaultdict(list)
    for o in pool:
        if o.industry:
            groups[o.industry].append(o)
    rel_s, rel_f = [], []
    for members in groups.values():
        if len(members) < MIN_GROUP:
            continue
        m = median([math.log(o.base / o.price) for o in members]) or 0.0
        for o in members:
            rel_s.append(math.log(o.base / o.price) - m)
            rel_f.append(o.forward[horizon])
    out["model: industry-relative"] = (rel_s, rel_f)
    ep = [o for o in pool if o.earnings_yield is not None]
    out["earnings yield"] = ([o.earnings_yield for o in ep], [o.forward[horizon] for o in ep])  # type: ignore[misc]
    bp = [o for o in pool if o.book_to_price is not None and o.book_to_price > 0]
    out["book to price"] = ([o.book_to_price for o in bp], [o.forward[horizon] for o in bp])  # type: ignore[misc]
    return out


def outcome_accounting(outs: Sequence[Out], horizon: int) -> dict[str, Any]:
    """How the later return of every valued company was measured at `horizon`: `priced`, `restructured` (a demerger, scheme, capital reduction or unreadable ratio: unknown from the price alone),
    `no_end_price` (stopped trading before the end: proceeds unknown) or `not_yet_known`, with the share of the companies whose outcome WAS due that are unknown. Companies without a recorded outcome (an
    approximate study) are `unrecorded`: that study cannot say."""
    counts = Counter(o.outcome.get(horizon, "unrecorded") for o in outs)
    due = counts["priced"] + counts["restructured"] + counts["no_end_price"]
    return {**{k: counts.get(k, 0) for k in ("priced", "restructured", "no_end_price", "not_yet_known", "unrecorded")},
            "unknown_share": (counts["restructured"] + counts["no_end_price"]) / due if due else None}


def zero_recovery_sensitivity(outs: Sequence[Out], horizon: int, min_cap: float = MIN_MARKET_CAP_CRORE) -> dict[str, dict[str, Any]]:
    """The signal metrics again with every company that stopped trading counted at a return of -100% (the locked policy's assumed total-loss scenario, `zero_recovery`). A NAMED sensitivity beside the
    measurable-only result, never the result itself and not a bound on it: recovery or liquidation proceeds could be positive. The other scenario, valuing each at its last traded close, is not computed here
    because this study does not hold that close. Restructured names stay out of both: their return is unknown."""
    filled = [replace(o, forward={**o.forward, horizon: -1.0}) if o.outcome.get(horizon) == "no_end_price" else o for o in outs]
    return forward_metrics(filled, horizon, min_cap)


def forward_metrics(outs: Sequence[Out], horizon: int = 6, min_cap: float = MIN_MARKET_CAP_CRORE) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, (sig, fwd) in _signals(outs, horizon, min_cap).items():
        q = quintile_returns(sig, fwd)
        result[name] = {"n": len(sig), "ic": spearman(sig, fwd), "quintiles": q, "spread": (q[4] - q[0]) if q else None}
    return result


def summarize_ic(per_date: Sequence[float | None]) -> dict[str, float | None]:
    values = [v for v in per_date if v is not None]
    if len(values) < 2:
        return {"mean": values[0] if values else None, "t": None, "n": len(values), "positive": None}
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    return {"mean": mean, "t": mean / (sd / math.sqrt(len(values))) if sd else None, "n": len(values), "positive": sum(1 for v in values if v > 0) / len(values)}
