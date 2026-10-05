import copy
import json

from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.agents.base import Specialist
from athena.agents.earnings_intelligence import EARNINGS_INTELLIGENCE
from athena.agents.earnings_intelligence import PERSONA as EARNINGS_PERSONA
from athena.agents.moat_quality import MOAT_QUALITY
from athena.agents.moat_quality import PERSONA as MOAT_PERSONA
from athena.agents.valuation import PERSONA as VALUATION_PERSONA
from athena.agents.valuation import VALUATION
from athena.evaluation.checks import check_abstention
from athena.evaluation.schema import validate_specialist_output
from athena.metrics.fundamentals import build_fundamentals_packet

SPECS = {"valuation": VALUATION, "moat_quality": MOAT_QUALITY, "earnings_intelligence": EARNINGS_INTELLIGENCE}
FULL = build_fundamentals_packet("ACME", NOW, ACME, PRICE, NOW, index_history())


def bank_payload():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    for line in ("Operating Income", "EBIT", "Gross Profit", "Interest Expense"):
        del bank["annual"]["income"][line]
    for line in ("Current Assets", "Current Liabilities"):
        del bank["annual"]["balance"][line]
    return bank


BANK = build_fundamentals_packet("BANK", NOW, bank_payload(), PRICE, NOW, index_history())


class Narrator:
    """A fake model that cites the first two numeric figures in the packet it was shown."""

    def __init__(self):
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        metrics = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        cited = [(name, m["value"]) for name, m in metrics.items() if not isinstance(m["value"], str)][:2]
        text = " ".join(f"{name} is {value}." for name, value in cited)
        return json.dumps({"signal": "neutral", "confidence": 55, "reasoning": text})


def run(spec, packet):
    narrator = Narrator()
    return Specialist(spec, narrator).analyze(packet), narrator


def test_every_declared_figure_exists_in_a_full_packet_and_critical_and_optional_are_shown():
    for name, spec in SPECS.items():
        declared = set(spec.critical) | set(spec.optional) | set(spec.shows)
        assert declared <= set(FULL["metrics"]), (name, declared - set(FULL["metrics"]))
        assert set(spec.critical) | set(spec.optional) <= set(spec.shows), name
        assert spec.critical and not set(spec.critical) & set(spec.optional), name


def test_personas_encode_the_trd_rules_and_the_compliance_language():
    for persona in (VALUATION_PERSONA, MOAT_PERSONA, EARNINGS_PERSONA):
        assert "do not calculate your own" in persona and "price target" in persona and "not financial advice" in persona
        assert "bank or lender" in persona and "as_of" in persona
    for rule in ("Graham", "owner_earnings_yield", "peg", "trap"):
        assert rule in VALUATION_PERSONA
    assert "fraction between 0 and 1" in VALUATION_PERSONA and "0.01 means the market is near its cheapest" in VALUATION_PERSONA
    for rule in ("roe_minimum", "WHY a business is advantaged", "never name a moat source"):
        assert rule in MOAT_PERSONA
    for rule in ("accruals_ratio", "Sloan", "days_to_next_earnings", "data_quality_flags", "unverified"):
        assert rule in EARNINGS_PERSONA


def test_complete_data_gives_each_specialist_full_coverage_and_a_valid_grounded_output():
    for name, spec in SPECS.items():
        out, _ = run(spec, FULL)
        assert out["data_coverage"] == "full" and out["missing"] == [], name
        assert validate_specialist_output(out) == [], name


def test_each_specialist_is_shown_only_its_own_figures():
    _, valuation = run(VALUATION, FULL)
    _, moat = run(MOAT_QUALITY, FULL)
    _, earnings = run(EARNINGS_INTELLIGENCE, FULL)
    assert "pe_trailing" in valuation.prompts[0] and "roe_minimum" not in valuation.prompts[0]
    assert "accruals_ratio" not in valuation.prompts[0]
    assert "roe_minimum" in moat.prompts[0] and "pe_trailing" not in moat.prompts[0] and "beats_last_4" not in moat.prompts[0]
    assert "accruals_ratio" in earnings.prompts[0] and "pe_trailing" not in earnings.prompts[0]
    assert "roe_minimum" not in earnings.prompts[0]


def test_a_bank_is_not_marked_partial_for_ratios_that_never_apply_to_it():
    assert BANK["is_lender"] and BANK["not_applicable"]
    for name, spec in SPECS.items():
        out, _ = run(spec, BANK)
        assert (out["data_coverage"], out["missing"]) == ("full", []), (name, out["missing"])
    _, narrator = run(VALUATION, BANK)
    assert "not a data gap" in narrator.prompts[0] and "fcf_yield" in narrator.prompts[0]


def test_a_missing_critical_figure_makes_the_specialist_abstain_without_calling_the_model():
    no_price = build_fundamentals_packet("ACME", NOW, ACME, None, NOW, index_history())
    out, narrator = run(VALUATION, no_price)
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")
    assert narrator.prompts == []
    losing = copy.deepcopy(ACME)
    losing["quarterly"]["income"]["Net Income"] = {period: None for period in losing["quarterly"]["income"]["Net Income"]}
    out, narrator = run(EARNINGS_INTELLIGENCE, build_fundamentals_packet("ACME", NOW, losing, PRICE, NOW, index_history()))
    assert out["data_coverage"] == "insufficient" and narrator.prompts == []


def test_a_missing_optional_figure_makes_coverage_partial_and_names_it():
    thin = copy.deepcopy(ACME)
    thin["earnings_dates"] = []
    out, _ = run(EARNINGS_INTELLIGENCE, build_fundamentals_packet("ACME", NOW, thin, PRICE, NOW, index_history()))
    assert out["data_coverage"] == "partial" and {"eps_surprise_last", "days_to_next_earnings"} <= set(out["missing"])


def test_the_abstention_harness_passes_for_each_specialist():
    for name, spec in SPECS.items():
        specialist = Specialist(spec, Narrator(), fail_quiet=True)
        failures = check_abstention(
            lambda metrics: specialist.analyze({**FULL, "metrics": metrics}),
            FULL["metrics"], critical=list(spec.critical), optional=list(spec.optional),
        )
        assert failures == [], (name, failures)
