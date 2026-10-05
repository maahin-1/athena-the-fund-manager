from datetime import datetime, timezone

from athena.contracts import Record
from athena.evaluation.resolver_eval import (
    AMBIGUOUS,
    CORRECT,
    HAND_LABELED,
    MISSED,
    RESOLVE,
    SAFE,
    UNKNOWN,
    WRONG,
    ResolverCase,
    evaluate_resolver,
    format_report,
    judge_case,
    synthetic_cases,
)
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def make_resolver(equities, etfs=None):
    store = DataStore()
    for symbol, name in equities:
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    for symbol, name in (etfs or [("NIFTYBEES", "NIPINDETFNIFTYBEES")]):
        store.put(Record(ETF_DATASET, symbol, NOW, "x", {"name": name, "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


SMALL = make_resolver(
    [("SBIN", "State Bank of India"), ("SBILIFE", "SBI Life Insurance Company Limited"),
     ("SBICARD", "SBI Cards and Payment Services Limited"), ("TCS", "Tata Consultancy Services Limited")]
)


def outcome(query, kind, symbol=None, asset_class="equity"):
    return judge_case(SMALL, ResolverCase(query, "t", kind, asset_class if symbol else None, symbol))[0]


def test_resolve_cases():
    assert outcome("sbin", RESOLVE, "SBIN") == CORRECT
    assert outcome("sbin", RESOLVE, "TCS") == WRONG  # confidently resolved to something else
    assert outcome("SBI", RESOLVE, "SBIN") == SAFE  # asked, and the right answer was offered
    assert outcome("SBI", RESOLVE, "TCS") == MISSED  # asked, but the right answer was not offered
    assert outcome("ZZZZQQ", RESOLVE, "SBIN") == MISSED  # refused


def test_wrong_asset_class_counts_as_wrong():
    assert outcome("SBIN", RESOLVE, "SBIN", asset_class="etf") == WRONG


def test_ambiguous_cases_expect_a_question_not_a_guess():
    assert outcome("SBI", AMBIGUOUS) == CORRECT
    assert outcome("SBIN", AMBIGUOUS) == WRONG
    assert outcome("ZZZZQQ", AMBIGUOUS) == MISSED


def test_unknown_cases_expect_a_refusal():
    assert outcome("ZZZZQQ", UNKNOWN) == CORRECT
    assert outcome("SBI", UNKNOWN) == SAFE
    assert outcome("SBIN", UNKNOWN) == WRONG


def test_evaluate_aggregates_by_category_and_collects_failures():
    cases = [
        ResolverCase("sbin", "ticker", RESOLVE, "equity", "SBIN"),
        ResolverCase("sbin", "ticker", RESOLVE, "equity", "TCS"),
        ResolverCase("SBI", "prefix", RESOLVE, "equity", "SBIN"),
        ResolverCase("SBI", "prefix", RESOLVE, "equity", "TCS"),
    ]
    report = evaluate_resolver(SMALL, cases)
    assert report.total == 4
    assert (report.count(CORRECT), report.count(SAFE), report.count(WRONG), report.count(MISSED)) == (1, 1, 1, 1)
    assert report.rate(WRONG) == 0.25
    assert report.category_rate("prefix", CORRECT, SAFE) == 0.5
    assert [c.expected_symbol for c, _ in report.wrong_cases] == ["TCS"]
    assert [c.expected_symbol for c, _ in report.missed_cases] == ["TCS"]
    assert "4 cases | wrong 25.0%" in format_report(report)


def test_hand_labeled_cases_are_well_formed():
    assert len(HAND_LABELED) >= 20
    for case in HAND_LABELED:
        assert case.kind in (RESOLVE, AMBIGUOUS, UNKNOWN)
        assert (case.expected_symbol is not None) == (case.kind == RESOLVE)


WORDS = ["Alpha", "Bravo", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet", "Kilo"]
BIG = make_resolver([(f"{a[:3].upper()}{b[:3].upper()}", f"{a} {b} Industries Limited") for a in WORDS for b in WORDS])
BIG_INDEX = BIG._index


def test_synthetic_cases_are_deterministic_and_cover_every_category():
    first = synthetic_cases(BIG_INDEX, seed=3, per_category=10)
    assert first == synthetic_cases(BIG_INDEX, seed=3, per_category=10)
    assert first != synthetic_cases(BIG_INDEX, seed=4, per_category=10)
    categories = {c.category for c in first}
    assert categories == {"ticker_lower", "ticker_suffix", "exact_name", "typo", "prefix", "garbage"}
    assert sum(1 for c in first if c.category == "garbage") == 10


def test_synthetic_cases_are_valid_against_the_index():
    for case in synthetic_cases(BIG_INDEX, seed=3, per_category=10):
        if case.kind == RESOLVE:
            entry = BIG_INDEX.by_symbol[case.expected_symbol]
            assert entry.asset_class == case.expected_class
            if case.category == "prefix":
                assert case.query not in BIG_INDEX.by_symbol  # a prefix that is a real symbol would be mislabeled
        else:
            assert case.kind == UNKNOWN and case.query not in BIG_INDEX.by_symbol


def test_simple_synthetic_categories_are_all_correct_and_nothing_is_wrong():
    report = evaluate_resolver(BIG, synthetic_cases(BIG_INDEX, seed=3, per_category=10))
    for category in ("ticker_lower", "ticker_suffix", "exact_name"):
        assert report.category_rate(category, CORRECT) == 1.0, category
    assert report.count(WRONG) == 0, report.wrong_cases
