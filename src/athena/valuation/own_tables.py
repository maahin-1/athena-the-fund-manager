"""Industry tables built from the exchange's filings, as they stood on a day.

    python -m athena.valuation own-tables [--date YYYY-MM-DD ...]     build the tables for each date (default: the dates of the Damodaran tables held, and today)
    python -m athena.valuation own-divergence [--date YYYY-MM-DD]      set them against Damodaran's India tables and write the report

For a day D, each company's figures are worked out from the statement lines filed on or before D (a restatement filed later is not seen), its price is the raw close on or before D and
its share count the one its filings stated by D. Each industry (the company map's) is then summed the way Damodaran's aggregates are: P/E is the industry's market value over the
profit of its money-making firms, P/B is market value over book equity, margins and returns are sums over sums, sales to capital is revenue over invested capital, and the beta is the
average of the firms' own regression betas (weekly returns over two years against the NIFTY 500) unlevered at the industry's own debt to equity and tax rate and corrected for cash.
The rows go into the reference store as source `own`, under the same dataset and metric names his tables use, so a reader that takes the source as a parameter can switch over. Nothing
switches over here: the divergence report is for the owner to read first.

Limits: a share count is the company's stated count in its filings (see `fundamentals.shares`), so a stock whose count is stale or missing is left out of the sums that need it; banks and other
lenders get only the measures that mean something for them (P/E, P/B, ROE, beta); a company the map cannot place is in no industry."""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from athena.fundamentals import shares as share_choice
from athena.reference.store import DEFAULT_PATH as REFERENCE, ReferenceStore, StatRow
from athena.valuation import industry as ind
from athena.valuation.inputs import CompanyInputs, inputs_from_lines

SOURCE, REGION = "own", "India"
BENCHMARK = "Nifty 500"
BETA_WEEKS, BETA_MIN_WEEKS, WEEK = 104, 52, 5
PRICE_WINDOW_DAYS = 7
SCALE_LIMIT = 100.0  # a year's sales or profit this many times the company's market value and its book equity is a filing in the wrong unit, not a business
MIN_FIRMS = 1  # a smaller industry is stored with its firm count; the reader decides what is enough
# Damodaran's dataset.metric names for each measure we build
NAMES: dict[str, str] = {
    "pe": "pe.aggregate_mkt_cap_trailing_net_income_only_money_making_firms", "pb": "pbv.pbv", "roe": "roe.roe_unadjusted", "net_margin": "margin.net_margin",
    "operating_margin": "margin.pre_tax_unadjusted_operating_margin", "ebitda_margin": "margin.ebitda_sales", "price_sales": "ps.price_sales", "ev_sales": "ps.ev_sales",
    "ev_ebitda": "vebitda.ev_ebitda", "sales_to_capital": "capex.sales_invested_capital_ltm", "tax_rate": "taxrate.aggregate_tax_rate",
    "debt_to_capital": "dbtfund.market_debt_to_capital_unadjusted", "beta": "beta.beta", "unlevered_beta": "beta.unlevered_beta_corrected_for_cash",
}
LENDER_MEASURES = ("pe", "pb", "roe", "beta")


class Lines:
    """Every company's statement lines as filed, for rebuilding what was known on a day. Rows are `(symbol, basis, statement, period_start, period_end, key, value, filed)`."""

    def __init__(self, rows: Iterable[tuple[Any, ...]]):
        self._by: dict[tuple[str, str, str], list[tuple[Any, ...]]] = defaultdict(list)
        for symbol, basis, statement, start, end, key, value, filed in rows:
            self._by[(symbol, basis, statement)].append((filed, start, end, key, value))
        for held in self._by.values():
            held.sort(key=lambda r: r[0])

    def symbols(self) -> set[str]:
        return {s for s, _, _ in self._by}

    def asof(self, symbol: str, basis: str, statement: str, day: date) -> dict[tuple[date, date], dict[str, float]]:
        """The periods of one statement with each figure as the latest filing made on or before `day` gave it."""
        out: dict[tuple[date, date], dict[str, float]] = {}
        for filed, start, end, key, value in self._by.get((symbol, basis, statement), ()):
            if filed.date() > day:
                break
            out.setdefault((start, end), {})[key] = value
        return out


def load_lines(path: Path) -> Lines:
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "SELECT symbol, basis, statement, period_start, period_end, key, value, filed FROM statement_lines WHERE statement IN ('income', 'balance', 'cashflow') AND filed IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return Lines(rows)


def shares_asof(counts: Sequence[Mapping[str, Any]], day: date) -> float | None:
    """The company's share count from the filings made on or before `day` (the same choice `fundamentals.shares` makes for today)."""
    held = [c for c in counts if c.get("filed") is not None and c["filed"].date() <= day]
    return share_choice.choose(held).shares if held else None


def company_asof(lines: Lines, symbol: str, layout: str, counts: Sequence[Mapping[str, Any]], price: float | None, day: date) -> CompanyInputs | None:
    """The company's valuation inputs as they stood on `day` (consolidated results, else standalone), or None when nothing had been filed."""
    for basis in ("consolidated", "standalone"):
        income = lines.asof(symbol, basis, "income", day)
        balance = lines.asof(symbol, basis, "balance", day)
        if not income and not balance:
            continue
        got = inputs_from_lines(symbol, basis, {"layout": layout, "shares": shares_asof(counts, day)}, income, balance, lines.asof(symbol, basis, "cashflow", day), price)
        if got is not None:
            return got
    return None


# ---- betas -----------------------------------------------------------------------------------------------------------------------------------

def weekly_betas(px: Mapping[str, np.ndarray], market: np.ndarray, end: int, weeks: int = BETA_WEEKS, min_weeks: int = BETA_MIN_WEEKS, step: int = WEEK) -> dict[str, float]:
    """Each stock's beta: the slope of its weekly returns on the market's over the `weeks` weeks to day index `end` (a week is `step` trading days). A stock needs `min_weeks`
    weeks with a close at both ends."""
    marks = [end - step * k for k in range(weeks, -1, -1) if end - step * k >= 0]
    if len(marks) < min_weeks + 1:
        return {}
    m = market[marks]
    mr = m[1:] / m[:-1] - 1.0
    out: dict[str, float] = {}
    for symbol, series in px.items():
        s = series[marks]
        r = s[1:] / s[:-1] - 1.0
        ok = np.isfinite(r) & np.isfinite(mr)
        if ok.sum() < min_weeks:
            continue
        x, y = mr[ok], r[ok]
        var = float(np.var(x))
        if var > 0:
            out[symbol] = float(np.cov(x, y, bias=True)[0, 1] / var)
    return out


# ---- industry aggregates ------------------------------------------------------------------------------------------------------------------------

def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


@dataclass(frozen=True)
class Measure:
    value: float
    n: int


def misscaled(f: CompanyInputs) -> bool:
    """Whether a company's flows are out of all proportion to its own size, which is how a filing stated in the wrong unit shows: sales or profit that is `SCALE_LIMIT` times both its
    market value and its book equity. One such filing (an apparel maker with sales of five lakh crore) swamps the sums of its whole industry."""
    size = max(f.market_cap or 0.0, f.book_equity or 0.0)
    flow = max(abs(f.revenue or 0.0), abs(f.net_profit or 0.0))
    return size > 0 and flow > SCALE_LIMIT * size


def merge_share_classes(firms: Sequence[CompanyInputs]) -> list[CompanyInputs]:
    """One company listed under two symbols (ordinary and differential-voting shares) files one set of statements: its sums count them once, with the market values added. Two
    companies are the same when their sales, profit, book equity and borrowings are all identical and not all empty."""
    groups: dict[tuple[Any, ...], list[CompanyInputs]] = {}
    out: list[CompanyInputs] = []
    for f in firms:
        key = (f.revenue, f.net_profit, f.book_equity, f.borrowings)
        if all(v is None for v in key):
            out.append(f)
        else:
            groups.setdefault(key, []).append(f)
    for same in groups.values():
        caps = [g.market_cap for g in same if g.market_cap is not None]
        out.append(replace(same[0], market_cap=sum(caps) if caps else None) if len(same) > 1 else same[0])
    return out


def prepare(firms: Sequence[CompanyInputs]) -> tuple[list[CompanyInputs], int, int]:
    """The firms an industry is summed over, with the number set aside as misreported and the number of listings merged into another's."""
    sane = [f for f in firms if not misscaled(f)]
    merged = merge_share_classes(sane)
    return merged, len(firms) - len(sane), len(sane) - len(merged)


def aggregate(firms: Sequence[CompanyInputs], betas: Mapping[str, float], lender: bool) -> dict[str, Measure]:
    """One industry's table from its firms' figures. A measure that cannot be worked out is absent, never zero."""
    out: dict[str, Measure] = {}
    priced = [f for f in firms if f.market_cap is not None and f.market_cap > 0]

    def put(name: str, value: float | None, n: int) -> None:
        if value is not None and np.isfinite(value) and n >= MIN_FIRMS:
            out[name] = Measure(float(value), n)

    making = [f for f in priced if f.net_profit is not None and f.net_profit > 0]
    put("pe", _ratio(sum(f.market_cap for f in making), sum(f.net_profit for f in making)), len(making))
    booked = [f for f in priced if f.book_equity is not None and f.book_equity > 0]
    put("pb", _ratio(sum(f.market_cap for f in booked), sum(f.book_equity for f in booked)), len(booked))
    returns = [f for f in booked if f.net_profit is not None]
    put("roe", _ratio(sum(f.net_profit for f in returns), sum(f.book_equity for f in returns)) if returns else None, len(returns))
    own = [betas[f.symbol] for f in firms if f.symbol in betas]
    put("beta", statistics.fmean(own) if own else None, len(own))
    if not lender:
        sales = [f for f in priced if f.revenue is not None and f.revenue > 0]
        total_sales = sum(f.revenue for f in sales)
        put("price_sales", _ratio(sum(f.market_cap for f in sales), total_sales), len(sales))
        profit = [f for f in sales if f.net_profit is not None]
        put("net_margin", sum(f.net_profit for f in profit) / sum(f.revenue for f in profit) if profit else None, len(profit))
        operating = [f for f in sales if f.ebit is not None]
        put("operating_margin", sum(f.ebit for f in operating) / sum(f.revenue for f in operating) if operating else None, len(operating))
        ebitda = [f for f in sales if f.operating_profit is not None]
        put("ebitda_margin", sum(f.operating_profit for f in ebitda) / sum(f.revenue for f in ebitda) if ebitda else None, len(ebitda))
        levered = [f for f in sales if f.borrowings is not None and f.cash_and_bank is not None]
        ev = {f.symbol: f.market_cap + f.borrowings - f.cash_and_bank for f in levered}
        put("ev_sales", _ratio(sum(ev.values()), sum(f.revenue for f in levered)), len(levered))
        positive = [f for f in levered if f.operating_profit is not None and f.operating_profit > 0]
        put("ev_ebitda", _ratio(sum(ev[f.symbol] for f in positive), sum(f.operating_profit for f in positive)), len(positive))
        capital = [f for f in sales if f.invested_capital is not None and f.invested_capital > 0]
        put("sales_to_capital", _ratio(sum(f.revenue for f in capital), sum(f.invested_capital for f in capital)), len(capital))
        taxed = [f for f in firms if f.pbt is not None and f.pbt > 0 and f.tax is not None]
        put("tax_rate", _ratio(sum(f.tax for f in taxed), sum(f.pbt for f in taxed)), len(taxed))
        debt = [f for f in priced if f.borrowings is not None]
        put("debt_to_capital", _ratio(sum(f.borrowings for f in debt), sum(f.borrowings + f.market_cap for f in debt)), len(debt))
        tax = out["tax_rate"].value if "tax_rate" in out else None
        d_e = _ratio(sum(f.borrowings for f in debt), sum(f.market_cap for f in debt))
        if "beta" in out and tax is not None and d_e is not None:
            unlevered = out["beta"].value / (1 + (1 - tax) * d_e)
            cash = [f for f in debt if f.cash_and_bank is not None]
            cash_share = _ratio(sum(f.cash_and_bank for f in cash), sum(f.market_cap + f.borrowings for f in cash)) if cash else None
            put("unlevered_beta", unlevered / (1 - cash_share) if cash_share is not None and cash_share < 0.9 else unlevered, out["beta"].n)
    return out


def build_tables(
    day: date,
    firms_by_industry: Mapping[str, Sequence[CompanyInputs]],
    betas: Mapping[str, float],
    lenders: Callable[[str], bool] = ind.is_financial,
) -> list[StatRow]:
    """The reference-store rows for one day."""
    rows: list[StatRow] = []
    for industry, listed in firms_by_industry.items():
        firms, aside, merged = prepare(listed)
        note = f"own tables: filings filed by {day}, {len(firms)} companies" + (f", {aside} set aside as misreported" if aside else "") + (f", {merged} share classes merged" if merged else "")
        for measure, got in aggregate(firms, betas, lenders(industry)).items():
            dataset, metric = NAMES[measure].split(".", 1)
            rows.append(StatRow(SOURCE, REGION, dataset, industry, metric, got.value, got.n, day, note))
    return rows


# ---- divergence ---------------------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Gap:
    industry: str
    measure: str
    own: float
    his: float
    n_own: int
    n_his: int | None

    @property
    def ratio(self) -> float:
        return self.own / self.his if self.his else float("inf")


def divergence(ref: ReferenceStore, day: date) -> list[Gap]:
    """Our tables for `day` against Damodaran's in force on that day, for every industry and measure both have (ratios only where his value is positive)."""
    out: list[Gap] = []
    for industry in ref.industries(SOURCE, REGION):
        ours = ref.industry_stats(industry, SOURCE, REGION, day)
        his = ref.industry_stats(industry, "damodaran", REGION, day)
        for measure, name in NAMES.items():
            if name in ours and name in his and his[name][0] > 0:
                out.append(Gap(industry, measure, ours[name][0], his[name][0], ours[name][1] or 0, his[name][1]))
    return out


def summarize(gaps: Sequence[Gap], band: float = 0.25) -> dict[str, dict[str, float]]:
    """Per measure: how many industries, the median ratio of ours to his, and the share within `band` of his."""
    by: dict[str, list[Gap]] = defaultdict(list)
    for g in gaps:
        by[g.measure].append(g)
    return {
        m: {"industries": len(v), "median_ratio": statistics.median(g.ratio for g in v), "within_band": sum(1 for g in v if abs(g.ratio - 1) <= band) / len(v)}
        for m, v in by.items()
    }


def report(gaps: Sequence[Gap], day: date, band: float = 0.25, enough: int = 10) -> str:
    """The divergence report as text: the summary per measure, then the industries with a firm count of at least `enough` that are furthest from his figures."""
    lines = [f"# Our industry tables against Damodaran's India tables, {day}", "", f"Within {band:.0%} means our figure is between {1 - band:.2f} and {1 + band:.2f} times his.", ""]
    lines += ["| measure | industries | median ours/his | within band |", "|---|---|---|---|"]
    for measure, s in sorted(summarize(gaps, band).items()):
        lines.append(f"| {measure} | {s['industries']:.0f} | {s['median_ratio']:.2f} | {s['within_band']:.0%} |")
    lines += ["", f"## Furthest from his figures (industries with at least {enough} firms of ours)", "", "| industry | measure | ours | his | ratio | our firms | his firms |", "|---|---|---|---|---|---|---|"]
    far = sorted((g for g in gaps if g.n_own >= enough), key=lambda g: -abs(np.log(max(g.ratio, 1e-9))))[:25]
    for g in far:
        lines.append(f"| {g.industry} | {g.measure} | {g.own:.3f} | {g.his:.3f} | {g.ratio:.2f} | {g.n_own} | {g.n_his} |")
    return "\n".join(lines) + "\n"


# ---- command line -------------------------------------------------------------------------------------------------------------------------------

def _prices(warehouse: Path, day: date) -> dict[str, float]:
    import duckdb

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return dict(
            con.execute("SELECT symbol, arg_max(close, day) FROM bars WHERE day <= ? AND day > ? GROUP BY symbol", [day, day - timedelta(days=PRICE_WINDOW_DAYS)]).fetchall()
        )
    finally:
        con.close()


def _panel(warehouse: Path, symbols: Sequence[str]):
    """Adjusted closes on a common list of days, and the benchmark's close on the same days."""
    from athena.evaluation.walkforward import Panel
    from athena.trading_calendar import ist_date
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import Warehouse

    with Warehouse(warehouse, read_only=True, wait_seconds=300) as w:
        history = adjusted_history(w)
        series = {s: [(ist_date(b.timestamp), b.close) for b in history(s)] for s in symbols}
        market = dict(w._con.execute("SELECT day, close FROM index_bars WHERE index_name = ? AND close IS NOT NULL", [BENCHMARK]).fetchall())
    panel = Panel.build({s: rows for s, rows in series.items() if rows})
    bench = np.array([market.get(d, np.nan) for d in panel.days])
    return panel, bench


DEFAULT_REPORT = Path(".athena") / "reports" / "industry-divergence.md"


def run_divergence(reference: str, dates: Sequence[str] | None, out: str) -> int:
    with ReferenceStore(reference, read_only=True) as ref:
        day = date.fromisoformat(dates[0]) if dates else ref.latest_stat_date("damodaran", REGION) or date.today()
        text = report(divergence(ref, day), day)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def run_tables(reference: str, fundamentals: str, warehouse: str, dates: Sequence[str] | None) -> int:
    from athena.fundamentals.store import open_read

    args = argparse.Namespace(reference=reference, fundamentals=fundamentals, warehouse=warehouse, date=dates)
    with ReferenceStore(args.reference, read_only=True) as ref:
        mapping = {s: v[0] for s, v in ref.company_industries().items()}
        his = ref.latest_stat_date("damodaran", REGION)
    days = [date.fromisoformat(d) for d in args.date] if args.date else [d for d in (his, date.today()) if d]
    lines = load_lines(Path(args.fundamentals))
    with open_read(args.fundamentals) as fund:
        layouts = {r[0]: r[1] for r in fund._con.execute("SELECT symbol, layout FROM companies").fetchall()}
        counts = defaultdict(list)
        for symbol in lines.symbols():
            counts[symbol] = fund.share_counts(symbol)
    panel, bench = _panel(Path(args.warehouse), sorted(lines.symbols()))
    index = {d: i for i, d in enumerate(panel.days)}
    for day in days:
        prices = _prices(Path(args.warehouse), day)
        end = max((i for d, i in index.items() if d <= day), default=None)
        betas = weekly_betas(panel.px, bench, end) if end is not None else {}
        by_industry: dict[str, list[CompanyInputs]] = defaultdict(list)
        for symbol in lines.symbols():
            industry = mapping.get(symbol)
            price = prices.get(symbol)
            if industry is None or price is None:
                continue
            got = company_asof(lines, symbol, layouts.get(symbol) or "indas", counts[symbol], price, day)
            if got is not None:
                by_industry[industry].append(got)
        rows = build_tables(day, by_industry, betas)
        with ReferenceStore(args.reference) as ref:
            ref.put_stats(rows)
        print(f"{day}: {len(rows)} figures for {len(by_industry)} industries from {sum(len(v) for v in by_industry.values())} companies, {len(betas)} betas", file=sys.stdout, flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    from athena.fundamentals.store import DEFAULT_PATH as FUND_PATH
    from athena.valuation.cli import WAREHOUSE

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("what", choices=["own-tables", "own-divergence"])
    parser.add_argument("--date", action="append", help="YYYY-MM-DD; repeat for several")
    parser.add_argument("--reference", default=str(REFERENCE))
    parser.add_argument("--fundamentals", default=str(FUND_PATH))
    parser.add_argument("--warehouse", default=str(WAREHOUSE))
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)
    if args.what == "own-divergence":
        return run_divergence(args.reference, args.date, args.out)
    return run_tables(args.reference, args.fundamentals, args.warehouse, args.date)


if __name__ == "__main__":
    raise SystemExit(main())
