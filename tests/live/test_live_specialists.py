import pytest

from athena.cli import live_orchestrator
from athena.contracts import AthenaError
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import OK
from athena.orchestrator.report import format_result

pytestmark = pytest.mark.live

FUNDAMENTAL = {"valuation", "moat_quality", "earnings_intelligence"}


@pytest.fixture(scope="module")
def orchestrator():
    try:
        return live_orchestrator()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


def test_live_stock_is_analysed_by_the_fundamentals_specialists_on_real_data(orchestrator):
    result = orchestrator.analyze("TCS")
    print("\n" + format_result(result))
    assert result.status == OK and validate_judge_verdict(result.verdict) == []
    # a free-tier model can fail validation twice for one specialist; the others must still have answered
    assert len(FUNDAMENTAL & set(result.specialists)) >= 2, result.skipped
    for name, output in result.specialists.items():
        assert validate_specialist_output(output) == [], name


def test_live_bank_is_not_marked_partial_for_ratios_that_do_not_apply_to_banks(orchestrator):
    result = orchestrator.analyze("SBIN")
    print("\n" + format_result(result))
    assert result.status == OK
    ran = FUNDAMENTAL & set(result.specialists)
    assert len(ran) >= 2, result.skipped
    for name in ran:
        assert result.specialists[name]["data_coverage"] == "full" and result.specialists[name]["missing"] == [], name
