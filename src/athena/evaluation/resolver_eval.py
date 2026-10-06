from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from athena.contracts import UnknownInstrument
from athena.resolver import MAX_CANDIDATES, InstrumentIndex, InstrumentResolver, Resolution

CORRECT = "correct"  # right answer, no hedging
SAFE = "safe"  # asked the user and the right answer was in the candidates
WRONG = "wrong"  # confidently resolved to the wrong instrument: the failure that must not happen
MISSED = "missed"  # asked or refused without offering the right answer

RESOLVE = "resolve"  # expected_symbol should come back
AMBIGUOUS = "ambiguous"  # the resolver should ask, not guess
UNKNOWN = "unknown"  # the resolver should refuse


@dataclass(frozen=True)
class ResolverCase:
    query: str
    category: str
    kind: str
    expected_class: str | None = None
    expected_symbol: str | None = None


@dataclass
class ResolverReport:
    total: int = 0
    by_category: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    wrong_cases: list[tuple[ResolverCase, str]] = field(default_factory=list)
    missed_cases: list[tuple[ResolverCase, str]] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(counter[outcome] for counter in self.by_category.values())

    def rate(self, outcome: str) -> float:
        return self.count(outcome) / self.total if self.total else 0.0

    def category_rate(self, category: str, *outcomes: str) -> float:
        counter = self.by_category[category]
        size = sum(counter.values())
        return sum(counter[o] for o in outcomes) / size if size else 0.0


def judge_case(resolver: InstrumentResolver, case: ResolverCase) -> tuple[str, str]:
    """(outcome, what the resolver returned) for one case."""
    try:
        result = resolver.resolve(case.query)
    except UnknownInstrument as exc:
        got = f"refused: {exc}"
        return (CORRECT if case.kind == UNKNOWN else MISSED), got

    if isinstance(result, Resolution):
        got = f"resolved {result.asset_class}:{result.identifier} ({result.resolution_path})"
        if case.kind == RESOLVE:
            right = result.identifier == case.expected_symbol and result.asset_class == case.expected_class
            return (CORRECT if right else WRONG), got
        return WRONG, got

    candidates = [(c.asset_class, c.identifier) for c in result.candidates[:MAX_CANDIDATES]]  # the top of the ranking
    got = f"asked: {[ident for _, ident in candidates]}"
    if case.kind == AMBIGUOUS:
        return CORRECT, got
    if case.kind == UNKNOWN:
        return SAFE, got
    return (SAFE if (case.expected_class, case.expected_symbol) in candidates else MISSED), got


def evaluate_resolver(resolver: InstrumentResolver, cases: list[ResolverCase]) -> ResolverReport:
    report = ResolverReport()
    for case in cases:
        outcome, got = judge_case(resolver, case)
        report.total += 1
        report.by_category[case.category][outcome] += 1
        if outcome == WRONG:
            report.wrong_cases.append((case, got))
        elif outcome == MISSED:
            report.missed_cases.append((case, got))
    return report


def format_report(report: ResolverReport) -> str:
    lines = [f"{report.total} cases | wrong {report.rate(WRONG):.1%} | missed {report.rate(MISSED):.1%} | "
             f"correct {report.rate(CORRECT):.1%} | safe {report.rate(SAFE):.1%}"]
    for category in sorted(report.by_category):
        counter = report.by_category[category]
        lines.append(
            f"  {category:12} n={sum(counter.values()):3}  correct {counter[CORRECT]:3}  safe {counter[SAFE]:3}  "
            f"wrong {counter[WRONG]:3}  missed {counter[MISSED]:3}"
        )
    return "\n".join(lines)


def synthetic_cases(index: InstrumentIndex, seed: int = 7, per_category: int = 40) -> list[ResolverCase]:
    """Deterministic cases generated from the master lists themselves. They test self-consistency
    (could the resolver find an instrument given a degraded form of its own name?), not whether the
    lists are right, and they are no substitute for hand-labeled cases."""
    rng = random.Random(seed)
    entries = sorted(index.by_symbol.values(), key=lambda e: e.symbol)
    known_symbols = set(index.by_symbol)

    def sample(predicate) -> list:
        pool = [e for e in entries if predicate(e)]
        return rng.sample(pool, min(per_category, len(pool)))

    cases: list[ResolverCase] = []

    def add(category, entries_, make_query):
        for entry in entries_:
            cases.append(ResolverCase(make_query(entry), category, RESOLVE, entry.asset_class, entry.symbol))

    add("ticker_lower", sample(lambda e: True), lambda e: e.symbol.lower())
    add("ticker_suffix", sample(lambda e: True), lambda e: f"{e.symbol}.NS")
    add("exact_name", sample(lambda e: len(e.norm_name) >= 4), lambda e: e.name)
    add("typo", sample(lambda e: len(e.norm_name) >= 10), lambda e: e.name[: len(e.name) // 2] + e.name[len(e.name) // 2 + 1:])
    add("prefix", sample(lambda e: len(e.symbol) >= 6 and e.symbol.isalpha() and e.symbol[:4] not in known_symbols), lambda e: e.symbol[:4])

    while sum(1 for c in cases if c.category == "garbage") < per_category:
        junk = "".join(rng.choice("QXZJWKVYG") for _ in range(8))
        if junk not in known_symbols:
            cases.append(ResolverCase(junk, "garbage", UNKNOWN))
    return cases


HAND_LABELED: list[ResolverCase] = [
    ResolverCase("SBIN", "ticker", RESOLVE, "equity", "SBIN"),
    ResolverCase("reliance", "ticker", RESOLVE, "equity", "RELIANCE"),
    ResolverCase("TCS.NS", "ticker", RESOLVE, "equity", "TCS"),
    ResolverCase("NSE:INFY", "ticker", RESOLVE, "equity", "INFY"),
    ResolverCase("M&M", "ticker", RESOLVE, "equity", "M&M"),
    ResolverCase("BAJAJ-AUTO", "ticker", RESOLVE, "equity", "BAJAJ-AUTO"),
    ResolverCase("NIFTYBEES", "ticker", RESOLVE, "etf", "NIFTYBEES"),
    ResolverCase("GOLDBEES", "ticker", RESOLVE, "etf", "GOLDBEES"),
    ResolverCase("INE062A01020", "isin", RESOLVE, "equity", "SBIN"),
    ResolverCase("INF204KB14I2", "isin", RESOLVE, "etf", "NIFTYBEES"),
    ResolverCase("State Bank of India", "name", RESOLVE, "equity", "SBIN"),
    ResolverCase("Tata Consultancy Services", "name", RESOLVE, "equity", "TCS"),
    ResolverCase("Hindustan Unilever", "name", RESOLVE, "equity", "HINDUNILVR"),
    ResolverCase("Larsen and Toubro", "name", RESOLVE, "equity", "LT"),
    ResolverCase("Relience Industries", "typo", RESOLVE, "equity", "RELIANCE"),
    ResolverCase("RELIANC", "typo", RESOLVE, "equity", "RELIANCE"),
    ResolverCase("SBI", "ambiguous", AMBIGUOUS),
    ResolverCase("TATA", "ambiguous", AMBIGUOUS),
    ResolverCase("nifty 50 etf", "ambiguous", AMBIGUOUS),
    ResolverCase("ZZZZQQ", "garbage", UNKNOWN),
]
