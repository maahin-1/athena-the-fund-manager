import pytest

from athena.cli import live_orchestrator
from athena.contracts import AthenaError
from athena.evaluation.schema import validate_judge_verdict, validate_specialist_output
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK
from athena.orchestrator.report import format_result

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def orchestrator():
    try:
        return live_orchestrator()
    except AthenaError:
        pytest.skip("no LLM provider key is set")


@pytest.mark.parametrize("query, asset_class", [("SBIN", "equity"), ("NIFTYBEES", "etf")])
def test_live_orchestrator_analyzes_an_instrument_end_to_end(orchestrator, query, asset_class):
    result = orchestrator.analyze(query)
    print("\n" + format_result(result))
    assert result.status == OK, result.skipped
    assert result.resolution.asset_class == asset_class
    assert validate_judge_verdict(result.verdict) == []
    output = result.specialists["quant_technical"]
    assert validate_specialist_output(output) == [] and output["data_coverage"] == "full"


def test_live_orchestrator_asks_instead_of_guessing_on_an_ambiguous_name(orchestrator):
    result = orchestrator.analyze("SBI")
    assert result.status == NEEDS_CLARIFICATION and result.ambiguity.candidates
    assert result.specialists == {} and result.verdict is None
