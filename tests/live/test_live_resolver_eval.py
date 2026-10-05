import pytest

from athena.evaluation.resolver_eval import (
    CORRECT,
    HAND_LABELED,
    MISSED,
    SAFE,
    WRONG,
    evaluate_resolver,
    format_report,
    synthetic_cases,
)
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET, NseMasterLoader
from athena.resolver import InstrumentIndex, InstrumentResolver
from athena.store import DataStore

pytestmark = pytest.mark.live


def test_live_resolver_evaluation_meets_the_safety_and_recall_floors():
    store = DataStore()
    loader = NseMasterLoader(store)
    loader.refresh(EQUITY_DATASET)
    loader.refresh(ETF_DATASET)
    index = InstrumentIndex.from_store(store)
    report = evaluate_resolver(InstrumentResolver(index), HAND_LABELED + synthetic_cases(index))
    print("\n" + format_report(report))

    assert report.count(WRONG) == 0, report.wrong_cases  # never silently misroute
    assert report.rate(MISSED) <= 0.02, report.missed_cases[:5]
    for category in report.by_category:
        floor = 0.85 if category == "prefix" else 0.95
        assert report.category_rate(category, CORRECT, SAFE) >= floor, (category, report.by_category[category])
