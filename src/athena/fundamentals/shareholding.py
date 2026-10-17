"""A shareholding-pattern filing as the share of each holder group: promoters, foreign and domestic institutions, government, the public, and the
number of shareholders. The exchange files one XBRL per quarter; each holder group is a context named for it, and the figures are shares held and
number of holders. Two layouts exist: from about mid-2024 the contexts end `_ContextI` and the share percentage is a fraction, before that they end
`I` and it is a percentage, with a few differently spelled names. The percentage here is always worked out from the shares held over the total
shares, so the two layouts agree."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from defusedxml import ElementTree

# key -> the exchange's category names, tried in order (spellings differ between layouts)
CATEGORIES: dict[str, tuple[str, ...]] = {
    "promoters": ("ShareholdingOfPromoterAndPromoterGroup",),
    "fii": ("InstitutionsForeign",),
    "dii": ("InstitutionsDomestic",),
    "government": ("Governments", "Goverments"),
    "public": ("NonInstitutions",),
    "mutual_funds": ("MutualFundsOrUTI",),
    "insurance": ("InsuranceCompanies",),
    "banks": ("Banks",),
    "pension_funds": ("ProvidentFundsOrPensionFunds",),
    "retail": ("ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakh",),
    "employee_trusts": ("EmployeeBenefitsTrusts",),
    "public_total": ("PublicShareholding",),
    "total": ("ShareholdingPattern",),
}
LABELS = {
    "promoters": "Promoters", "fii": "FIIs", "dii": "DIIs", "government": "Government", "public": "Public", "mutual_funds": "Mutual funds",
    "insurance": "Insurance companies", "banks": "Banks", "pension_funds": "Pension and provident funds", "retail": "Retail (up to Rs 2 lakh)",
    "employee_trusts": "Employee trusts",
}
SHARES, HOLDERS = "NumberOfShares", "NumberOfShareholders"


@dataclass(frozen=True)
class Group:
    shares: float
    holders: float | None


@dataclass(frozen=True)
class Holding:
    symbol: str
    isin: str
    period_end: date
    groups: dict[str, Group] = field(default_factory=dict)

    def percent(self, key: str) -> float | None:
        total = self.groups.get("total")
        group = self.groups.get(key)
        if not total or not total.shares or group is None:
            return None
        return group.shares / total.shares * 100


def _number(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def parse_shareholding(text: str) -> Holding:
    """A shareholding filing as `Holding`. Raises ValueError for anything that is not one or states no date."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise ValueError(f"not an XBRL filing: {exc}") from exc
    facts: dict[str, dict[str, str]] = {}
    for element in root:
        ref = element.get("contextRef")
        if ref is not None and (element.text or "").strip():
            facts.setdefault(ref.lower(), {})[element.tag.rsplit("}", 1)[-1]] = element.text.strip()
    if not any(tag == "DateOfReport" for got in facts.values() for tag in got):
        raise ValueError("not a shareholding filing: no report date")
    main = {tag: value for got in facts.values() for tag, value in got.items() if tag in ("DateOfReport", "Symbol", "ISIN")}
    try:
        period_end = date.fromisoformat(main["DateOfReport"][:10])
    except ValueError as exc:
        raise ValueError(f"unreadable report date {main['DateOfReport']!r}") from exc
    groups: dict[str, Group] = {}
    for key, names in CATEGORIES.items():
        for name in names:
            got = facts.get(f"{name}_contexti".lower()) or facts.get(f"{name}i".lower())
            shares = _number(got.get(SHARES)) if got else None
            if shares is not None:
                groups[key] = Group(shares, _number(got.get(HOLDERS)))
                break
    if "total" not in groups:
        raise ValueError("the filing has no total shareholding")
    return Holding(main.get("Symbol", "").strip(), main.get("ISIN", "").strip(), period_end, groups)
