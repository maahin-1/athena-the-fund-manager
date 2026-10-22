"""A company in several businesses, valued by what it is made of: the benchmarks blended by segment, and a financial arm or an investment book taken out of the operating figures.

A results filing reports segments (`athena.fundamentals.segments`). Each is read by written rules (and by the owner's own readings in the reference store, which win) as an operating
business in one of Damodaran's industries, a financial business, an investment book, something to leave out (other, unallocated, eliminations) or a geography. Then:

  blend      two or more operating industries: every benchmark the model uses is the mean of the industries' figures. Operating measures (margin, growth, sales to capital, tax,
             working capital) are weighted by segment revenue; risk and return measures (betas, returns, multiples) by segment profit (revenue when none is positive).
             Used only when at least 60% of the operating revenue is read.
  carve out  a financial arm (or an investment segment) holding at least a tenth of the segments' assets is taken out of revenue, operating profit, debt and invested capital, and its
             book equity is added as a non-operating asset (a financial arm at its industry's price to book; an investment book is already in the balance sheet's investments).
             Segment profit shares scale the operating profit, so this is approximate, and it says so.

A filing that reports one segment, segments too old for the latest results, or mostly geographies, leaves the company as it is. Lenders and insurers are not touched."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from athena.reference.store import ReferenceStore
from athena.valuation import industry as ind
from athena.valuation.industry import IndustryContext
from athena.valuation.inputs import CompanyInputs

CRORE = 1e7
LENDER_LAYOUTS = ("bank", "finance", "insurer")
MIN_COVERAGE = 0.60  # of the operating revenue, to blend
CARVE_ASSET_SHARE = 0.10  # of all segment assets, for a financial or investment segment to be taken out
STALE_DAYS = 600  # segments older than this before the company's latest period are not used
PB_BOUNDS = (0.5, 4.0)

DAMODARAN_INDUSTRIES = frozenset(
    "Advertising|Aerospace/Defense|Air Transport|Apparel|Auto & Truck|Auto Parts|Bank (Money Center)|Banks (Regional)|Beverage (Alcoholic)|Beverage (Soft)|Broadcasting|"
    "Brokerage & Investment Banking|Building Materials|Business & Consumer Services|Cable TV|Chemical (Basic)|Chemical (Diversified)|Chemical (Specialty)|Coal & Related Energy|"
    "Computer Services|Computers/Peripherals|Construction Supplies|Diversified|Drugs (Biotechnology)|Drugs (Pharmaceutical)|Education|Electrical Equipment|"
    "Electronics (Consumer & Office)|Electronics (General)|Engineering/Construction|Entertainment|Environmental & Waste Services|Farming/Agriculture|"
    "Financial Svcs. (Non-bank & Insurance)|Food Processing|Food Wholesalers|Furn/Home Furnishings|Green & Renewable Energy|Healthcare Products|Healthcare Support Services|"
    "Heathcare Information and Technology|Homebuilding|Hospitals/Healthcare Facilities|Hotel/Gaming|Household Products|Information Services|Insurance (General)|Insurance (Life)|"
    "Insurance (Prop/Cas.)|Investments & Asset Management|Machinery|Metals & Mining|Office Equipment & Services|Oil/Gas (Integrated)|Oil/Gas (Production and Exploration)|"
    "Oil/Gas Distribution|Oilfield Svcs/Equip.|Packaging & Container|Paper/Forest Products|Power|Precious Metals|Publishing & Newspapers|R.E.I.T.|Real Estate (Development)|"
    "Real Estate (General/Diversified)|Real Estate (Operations & Services)|Recreation|Reinsurance|Restaurant/Dining|Retail (Automotive)|Retail (Building Supply)|"
    "Retail (Distributors)|Retail (General)|Retail (Grocery and Food)|Retail (REITs)|Retail (Special Lines)|Rubber& Tires|Semiconductor|Semiconductor Equip|Shipbuilding & Marine|"
    "Shoe|Software (Entertainment)|Software (Internet)|Software (System & Application)|Steel|Telecom (Wireless)|Telecom. Equipment|Telecom. Services|Tobacco|Transportation|"
    "Transportation (Railroads)|Trucking|Utility (General)|Utility (Water)".split("|")
)
FINANCIAL = frozenset(
    {"Financial Svcs. (Non-bank & Insurance)", "Brokerage & Investment Banking", "Investments & Asset Management", "Insurance (General)", "Insurance (Life)", "Insurance (Prop/Cas.)",
     "Reinsurance", "Bank (Money Center)", "Banks (Regional)"}
)

IGNORE = re.compile(
    r"^\s*(0|na|n\.a\.?|nil|-+|others?|other (segments?|businesses|operations|income|activities)|un-?allocated.*|unallocable.*|exceptional.*|discontinued.*|inter[- ]?segment.*|"
    r"elimination.*|adjustment.*|add\b.*|less\b.*|total.*|reconcil.*|common.*|corporate( and other.*)?|net .*)\s*$",
    re.I,
)
GEOGRAPHY = re.compile(
    r"^\s*(india|domestic|home|exports?|overseas|international|rest of (the )?world|row|europe|usa|u\.s\.a?\.?|united states|north america|asia( pacific)?|apac|china|uk|u\.k\.|"
    r"middle east|africa|americas|within india|outside india|other countries|foreign)\s*$",
    re.I,
)
# first match wins; the financial and investment rules come first so that "retail finance" is a lender and "retail" is a shop
RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bbank", "Banks (Regional)", "financial"),
    (r"insur|assurance", "Insurance (General)", "financial"),
    (r"broking|brokerage|securities|stock exchange", "Brokerage & Investment Banking", "financial"),
    (r"asset management|wealth|mutual fund|portfolio", "Investments & Asset Management", "financial"),
    (r"financ|nbfc|lending|loans?\b|housing loan|credit|microfin|leasing|\bfintech\b", "Financial Svcs. (Non-bank & Insurance)", "financial"),
    (r"^investments?\b|investing|treasury", "Investments & Asset Management", "investment"),
    (r"oil (to|&|and) chem|o2c|refin|petrochem|oil marketing|petroleum product", "Oil/Gas (Integrated)", "operating"),
    (r"exploration|upstream|oil (&|and) gas|e&p|hydrocarbon", "Oil/Gas (Production and Exploration)", "operating"),
    (r"gas (distribution|utilit)|city gas|\blng\b|pipeline", "Oil/Gas Distribution", "operating"),
    (r"digital|telecom|mobile|broadband|wireless|connectivity|\bjio\b", "Telecom. Services", "operating"),
    (r"software|it services|it & tech|information technology|technology services|\bbpo\b|\bbpm\b|consulting", "Computer Services", "operating"),
    (r"pharma|drug|formulation|\bapi\b|generics|biotech|healthcare products", "Drugs (Pharmaceutical)", "operating"),
    (r"hospital|diagnostic|healthcare serv", "Hospitals/Healthcare Facilities", "operating"),
    (r"retail|stores|e-?commerce|supermarket|apparel retail", "Retail (General)", "operating"),
    (r"hotel|hospitality|resorts?\b|lodging|gaming|casino", "Hotel/Gaming", "operating"),
    (r"restaurant|quick service|\bqsr\b|food service", "Restaurant/Dining", "operating"),
    (r"real estate|realty|property|properties|residential|commercial (space|property)|leasing of space|township", "Real Estate (Development)", "operating"),
    (r"cement|ready[- ]?mix|concrete|building material|\btiles\b|ceramic|sanitary|bath", "Building Materials", "operating"),
    (r"epc|projects?\b|infrastructure|civil|construction|engineering (and|&) construction", "Engineering/Construction", "operating"),
    (r"renewable|solar|wind|green energy|new energy|hydro", "Green & Renewable Energy", "operating"),
    (r"power|electricity|thermal|generation|transmission|distribution of electric|energy", "Power", "operating"),
    (r"coal", "Coal & Related Energy", "operating"),
    (r"airline|aviation|air transport", "Air Transport", "operating"),
    (r"airports?\b|\bports?\b|logistic|shipping|marine|freight|transport|warehous|container", "Transportation", "operating"),
    (r"railway|rail\b", "Transportation (Railroads)", "operating"),
    (r"\broads?\b|highway|toll|engineering|building", "Engineering/Construction", "operating"),
    (r"steel|iron|sponge|pig iron|ferro|alloy", "Steel", "operating"),
    (r"aluminium|aluminum|copper|zinc|metal|mining|mineral|ore\b", "Metals & Mining", "operating"),
    (r"chemical|agrochem|fertili[sz]er|pesticide|dye|pigment|specialty", "Chemical (Diversified)", "operating"),
    (r"paint|coating", "Chemical (Specialty)", "operating"),
    (r"textile|yarn|fabric|garment|apparel|spinning|weaving|denim", "Apparel", "operating"),
    (r"paper|packag|board|pulp|forest", "Paper/Forest Products", "operating"),
    (r"sugar|ethanol|molasses|co-?gen", "Food Processing", "operating"),
    (r"distiller|liquor|alcohol|brewer|spirits|wine", "Beverage (Alcoholic)", "operating"),
    (r"cigarette|tobacco", "Tobacco", "operating"),
    (r"fmcg|food|dairy|edible|oil seeds|snack|bakery|beverage", "Food Processing", "operating"),
    (r"farm equipment|tractor|agri(cultural)? (machinery|equipment)", "Machinery", "operating"),
    (r"agri|farm|seed|plantation|tea\b|coffee|fisher|poultry", "Farming/Agriculture", "operating"),
    (r"auto component|automotive component|auto ancillar|tyre|tire|rubber", "Auto Parts", "operating"),
    (r"automotive|automobile|vehicle|two[- ]wheeler|passenger|commercial vehicle|tractor", "Auto & Truck", "operating"),
    (r"electric(al)? (equipment|products)|cables?|wires?|switchgear|transformers?|motors?", "Electrical Equipment", "operating"),
    (r"electronic|ems\b|semiconductor|consumer durable|appliance", "Electronics (General)", "operating"),
    (r"machinery|machine|equipment|capital goods|pumps?|valves?|bearings?|industrial products", "Machinery", "operating"),
    (r"defen[cs]e|aerospace|shipbuild", "Aerospace/Defense", "operating"),
    (r"media|broadcast|television|tv\b|radio|news|publishing|print", "Broadcasting", "operating"),
    (r"entertainment|film|cinema|multiplex|music|content", "Entertainment", "operating"),
    (r"education|school|training|learning|university", "Education", "operating"),
    (r"advertis|marketing|event", "Advertising", "operating"),
    (r"water|waste|environment|sewage", "Environmental & Waste Services", "operating"),
    (r"personal care|home care|household|cosmetic|soap|detergent", "Household Products", "operating"),
    (r"furniture|home furnish|mattress|furnishing", "Furn/Home Furnishings", "operating"),
    (r"trading|distribution|wholesale|marketing of", "Retail (Distributors)", "operating"),
    (r"services?\b", "Business & Consumer Services", "operating"),
    (r"manufactur", "Diversified", "operating"),
)
_COMPILED = tuple((re.compile(p, re.I), industry, kind) for p, industry, kind in RULES)

# measures weighted by revenue; every other benchmark is weighted by profit
REVENUE_WEIGHTED = frozenset(
    {"operating_margin", "ebitda_margin", "net_margin", "expected_revenue_growth_5y", "revenue_cagr_5y", "sales_to_capital", "tax_rate", "non_cash_wc_to_sales", "reinvestment_rate",
     "fundamental_growth", "price_sales", "ev_sales"}
)


@dataclass(frozen=True)
class Reading:
    kind: str  # "operating", "financial", "investment", "ignored", "geography" or "unmapped"
    industry: str | None = None


def classify(name: str, overrides: dict[str, str] | None = None) -> Reading:
    """What a segment name is: the owner's reading if there is one, else the first written rule that fits."""
    key = name.strip().lower()
    if overrides and key in overrides:
        chosen = overrides[key]
        if chosen.lower() == "ignore":
            return Reading("ignored")
        return Reading("financial" if chosen in FINANCIAL else "operating", chosen)
    if IGNORE.match(key):
        return Reading("ignored")
    if GEOGRAPHY.match(key):
        return Reading("geography")
    for pattern, industry, kind in _COMPILED:
        if pattern.search(key):
            return Reading(kind, industry)
    return Reading("unmapped")


@dataclass(frozen=True)
class Prepared:
    company: CompanyInputs
    industry: IndustryContext | None
    notes: tuple[str, ...] = ()
    mix: tuple[tuple[str, float], ...] = ()
    carved: bool = False


def _blend(parts: list[tuple[IndustryContext, float, float]], label: str, flags: tuple[str, ...]) -> IndustryContext:
    fields = {name for ctx, _, _ in parts for name in ctx.values}
    values: dict[str, float] = {}
    for name in fields:
        weights = [(ctx.values[name], rev if name in REVENUE_WEIGHTED else val) for ctx, rev, val in parts if name in ctx.values]
        total = sum(w for _, w in weights)
        if total > 0:
            values[name] = sum(v * w for v, w in weights) / total
    dates = [ctx.data_as_of for ctx, _, _ in parts if ctx.data_as_of]
    inherited = tuple(dict.fromkeys(f for ctx, _, _ in parts for f in ctx.flags))
    return IndustryContext(label, "segment mix", 1.0, None, min(dates) if dates else None, values, (*inherited, *flags))


def _pct(x: float) -> str:
    return f"{x:.0%}"


def prepare(fund: Any, ref: ReferenceStore, company: CompanyInputs, own: IndustryContext | None, on_date: date) -> Prepared:
    """The company and industry context to value: as they are, or blended by segment and with a financial arm taken out."""
    if company.layout in LENDER_LAYOUTS:
        return Prepared(company, own)
    end, rows = fund.segments(company.symbol, company.basis)
    if not rows or end is None or (company.period_end - end).days > STALE_DAYS:
        return Prepared(company, own)
    overrides = ref.segment_industries(company.symbol)
    segs = []
    for r in rows:
        reading = classify(r["name"], overrides)
        segs.append({**{k: (v / CRORE if k in ("revenue", "profit", "assets", "liabilities") and v is not None else v) for k, v in r.items()}, "kind": reading.kind, "industry": reading.industry})
    counted = [s for s in segs if s["kind"] in ("operating", "financial", "investment", "unmapped")]
    revenue_all = sum(max(s["revenue"] or 0.0, 0.0) for s in counted)
    geography = sum(max(s["revenue"] or 0.0, 0.0) for s in segs if s["kind"] == "geography")
    if revenue_all <= 0 or geography > revenue_all:
        return Prepared(company, own)

    notes: list[str] = []
    industry_context = own
    mix: tuple[tuple[str, float], ...] = ()
    operating = [s for s in segs if s["kind"] == "operating"]
    unmapped = [s for s in segs if s["kind"] == "unmapped"]
    op_revenue = sum(max(s["revenue"] or 0.0, 0.0) for s in operating)
    unread = sum(max(s["revenue"] or 0.0, 0.0) for s in unmapped)
    distinct = sorted({s["industry"] for s in operating})
    if len(distinct) >= 2 and op_revenue / max(op_revenue + unread, 1e-9) >= MIN_COVERAGE:
        by_rev: dict[str, float] = {}
        by_profit: dict[str, float] = {}
        for s in operating:
            by_rev[s["industry"]] = by_rev.get(s["industry"], 0.0) + max(s["revenue"] or 0.0, 0.0)
            by_profit[s["industry"]] = by_profit.get(s["industry"], 0.0) + max(s["profit"] or 0.0, 0.0)
        rev_total, profit_total = sum(by_rev.values()), sum(by_profit.values())
        parts = []
        for name in distinct:
            ctx = ind.for_industry(ref, name, "segment mix", 1.0, on_date)
            if ctx is None:
                continue
            rev_w = by_rev[name] / rev_total if rev_total > 0 else 0.0
            val_w = by_profit[name] / profit_total if profit_total > 0 else rev_w
            parts.append((ctx, rev_w, val_w))
        if len(parts) >= 2:
            shares = sorted(((c.industry, rw) for c, rw, _ in parts), key=lambda t: -t[1])
            mix = tuple(shares)
            summary = ", ".join(f"{n} {_pct(w)}" for n, w in shares)
            extra = [f"business mix from the segments of {end}: {summary}; operating measures weighted by revenue, risk and return measures by profit"]
            if unmapped:
                extra.append("segments left out of the mix because they could not be read: " + ", ".join(f"{s['name']} ({_pct(max(s['revenue'] or 0.0, 0.0) / revenue_all)} of revenue)" for s in unmapped))
            notes += extra
            industry_context = _blend(parts, "Mix: " + ", ".join(f"{n} {_pct(w)}" for n, w in shares), tuple(extra))
    elif len(distinct) >= 2:
        notes.append(f"segments could not be read well enough to blend benchmarks ({_pct(op_revenue / max(op_revenue + unread, 1e-9))} of operating revenue read): {', '.join(s['name'] for s in unmapped)}")

    company, carve_notes, carved = _carve_out(ref, company, segs, own, on_date)
    notes += carve_notes
    if industry_context is not None and notes:
        industry_context = replace(industry_context, flags=tuple(dict.fromkeys((*industry_context.flags, *notes))))
    return Prepared(company, industry_context, tuple(notes), mix, carved)


def _carve_out(ref: ReferenceStore, company: CompanyInputs, segs: list[dict[str, Any]], own: IndustryContext | None, on_date: date) -> tuple[CompanyInputs, list[str], bool]:
    taken = [s for s in segs if s["kind"] in ("financial", "investment")]
    assets_all = sum(max(s["assets"] or 0.0, 0.0) for s in segs if s["kind"] in ("operating", "financial", "investment", "unmapped"))
    if not taken or assets_all <= 0:
        return company, [], False
    taken_assets = sum(max(s["assets"] or 0.0, 0.0) for s in taken)
    if taken_assets / assets_all < CARVE_ASSET_SHARE:
        return company, [], False
    if None in (company.revenue, company.ebit, company.borrowings, company.invested_capital, company.interest):
        return company, ["a financial arm or investment book was found but the company's own figures are incomplete: it is not taken out"], False
    fin = [s for s in taken if s["kind"] == "financial"]
    inv = [s for s in taken if s["kind"] == "investment"]
    fin_assets = sum(max(s["assets"] or 0.0, 0.0) for s in fin)
    fin_liab = sum(max(s["liabilities"] or 0.0, 0.0) for s in fin)
    inv_assets = sum(max(s["assets"] or 0.0, 0.0) for s in inv)
    taken_rev = sum(max(s["revenue"] or 0.0, 0.0) for s in taken)
    profits = [(s["profit"] or 0.0) for s in segs if s["kind"] in ("operating", "financial", "investment", "unmapped")]
    total_profit = sum(profits)
    remaining_profit = sum((s["profit"] or 0.0) for s in segs if s["kind"] in ("operating", "unmapped"))
    revenue_op = company.revenue - taken_rev  # type: ignore[operator]
    invested_op = company.invested_capital - fin_assets - inv_assets  # type: ignore[operator]
    if revenue_op < 0.2 * company.revenue or invested_op < 0.1 * company.invested_capital or total_profit <= 0 or remaining_profit <= 0:  # type: ignore[operator]
        return company, ["a financial arm or investment book was found but taking it out leaves too little to value: it is left in and the figures are unreliable"], False
    share = min(max(remaining_profit / total_profit, 0.05), 1.0)
    borrowings_op = max(company.borrowings - fin_liab, 0.0)  # type: ignore[operator]
    interest_op = company.interest * (borrowings_op / company.borrowings) if company.borrowings else company.interest  # type: ignore[operator]
    arm_equity = max(fin_assets - fin_liab, 0.0)
    pb = 1.0
    if fin:
        weights: dict[str, float] = {}
        for s in fin:
            weights[s["industry"]] = weights.get(s["industry"], 0.0) + max(s["assets"] or 0.0, 0.0)
        total_w = sum(weights.values())
        got = 0.0
        for name, w in weights.items():
            ctx = ind.for_industry(ref, name, "segment mix", 1.0, on_date)
            value = ctx.get("pb") if ctx else None
            got += (value if value is not None else 1.0) * w
        pb = min(max(got / total_w, PB_BOUNDS[0]), PB_BOUNDS[1]) if total_w else 1.0
    adjusted = replace(
        company, revenue=revenue_op, ebit=company.ebit * share, interest=interest_op, borrowings=borrowings_op, invested_capital=invested_op,  # type: ignore[operator]
        investments=(company.investments or 0.0) + arm_equity * pb,
    )
    names = ", ".join(s["name"] for s in taken)
    note = (
        f"taken out of the operating figures: {names} (assets ₹{taken_assets:,.0f} cr, {taken_assets / assets_all:.0%} of the segments' assets); revenue ₹{taken_rev:,.0f} cr removed, "
        f"operating profit scaled to {share:.0%} by segment profit, debt reduced by the arm's liabilities ₹{fin_liab:,.0f} cr, invested capital by its assets; "
        + (f"its book equity ₹{arm_equity:,.0f} cr is added as a non-operating asset at {pb:.2f} times book" if fin else "an investment book is already counted in the balance sheet's investments")
        + " (approximate: segment figures are at the financial year-end, minorities in the arm stay at book)"
    )
    return adjusted, [note], True
