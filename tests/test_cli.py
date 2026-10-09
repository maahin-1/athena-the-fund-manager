import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from athena.cli import build_orchestrator, main
from athena.contracts import AthenaError, Bar, Record
from athena.evaluation.schema import validate_judge_verdict
from athena.loaders.nse_masters import EQUITY_DATASET, ETF_DATASET
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OK, OrchestrationResult
from athena.resolver import Ambiguity, Candidate, InstrumentIndex, InstrumentResolver, Resolution
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def make_resolver():
    store = DataStore()
    for symbol, name in (("SBIN", "State Bank of India"), ("TCS", "Tata Consultancy Services Limited")):
        store.put(Record(EQUITY_DATASET, symbol, NOW, "x", {"name": name, "isin": "INE000000000"}))
    store.put(Record(ETF_DATASET, "NIFTYBEES", NOW, "x", {"name": "NIPINDETFNIFTYBEES", "isin": "INF000000000"}))
    return InstrumentResolver(InstrumentIndex.from_store(store, now=NOW))


def bars(count=400):
    days, day = [], date(2026, 10, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar("X", datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1),
            100 + i * 0.5, 101 + i * 0.5, 99 + i * 0.5, 100 + i * 0.5, 1000.0, NOW, "t")
        for i, d in enumerate(reversed(days))
    ]


class Narrator:
    """A fake model that cites real figures from the packet it receives."""

    def complete(self, system, user):
        metrics = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        text = f"Last close {metrics['last_close']['value']} with RSI {metrics['rsi_14']['value']} and daily trend {metrics['trend_daily']['value']}."
        return json.dumps({"signal": "bullish", "confidence": 72, "reasoning": text})


class FakeLLMRouter:
    def __init__(self):
        self.roles = []

    def client_for(self, role):
        self.roles.append(role)
        return Narrator()


class FakeChain:
    def run(self, symbol, **kwargs):
        return SimpleNamespace(value=bars(), source="fake")


def test_offline_end_to_end_resolves_fetches_analyzes_and_blends():
    llm_router = FakeLLMRouter()
    orchestrator = build_orchestrator(make_resolver(), llm_router, FakeChain(), clock=lambda: NOW)
    result = orchestrator.analyze("sbin")
    assert llm_router.roles == ["specialist"]
    assert result.status == OK and result.resolution.identifier == "SBIN"
    assert result.specialists["quant_technical"]["data_coverage"] == "full"
    assert set(result.skipped) == {"valuation", "moat_quality", "earnings_intelligence"}
    assert validate_judge_verdict(result.verdict) == []
    assert result.verdict["verdict"] == "Buy" and result.verdict["conviction"] == 72


def test_an_etf_routes_to_quant_technical_and_notes_the_unbuilt_etf_analyst():
    result = build_orchestrator(make_resolver(), FakeLLMRouter(), FakeChain(), clock=lambda: NOW).analyze("NIFTYBEES")
    assert result.resolution.asset_class == "etf" and "quant_technical" in result.specialists
    assert result.skipped == {"etf_analyst": "not built yet"}


class StubOrchestrator:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.queries = result, error, []

    def analyze(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


def ok_result():
    resolution = Resolution("equity", "ticker", "SBIN", "State Bank of India", "", "exact", 1.0, (), ())
    verdict = {"verdict": "Hold", "conviction": 10, "key_risks": [], "resolution_path": "blend"}
    return OrchestrationResult(OK, "sbin", resolution, None, {}, {}, None, 0.0, verdict, ())


def test_main_joins_words_prints_the_report_and_returns_zero(capsys):
    stub = StubOrchestrator(ok_result())
    assert main(["state", "bank", "--env-file", "x.env"], factory=lambda env_file: stub) == 0
    assert stub.queries == ["state bank"]
    assert "Verdict: Hold" in capsys.readouterr().out


def test_main_returns_two_when_the_input_is_ambiguous(capsys):
    ambiguity = Ambiguity("sbi", (Candidate("equity", "SBIN", "State Bank of India", 0.8),), "several matches")
    stub = StubOrchestrator(OrchestrationResult(NEEDS_CLARIFICATION, "sbi", None, ambiguity, {}, {}, None, None, None, ()))
    assert main(["sbi"], factory=lambda env_file: stub) == 2
    assert "more than one instrument" in capsys.readouterr().out


def test_main_reports_athena_errors_and_bad_input_without_a_traceback(capsys):
    assert main(["sbin"], factory=lambda env_file: (_ for _ in ()).throw(AthenaError("no LLM provider key is set"))) == 1
    assert "error: no LLM provider key is set" in capsys.readouterr().out
    assert main(["sbin"], factory=lambda env_file: StubOrchestrator(error=ValueError("empty query"))) == 1


class OverlayStub:
    def __init__(self, result):
        self.result, self.calls = result, []

    def analyze(self, query, overlay=None):
        self.calls.append((query, overlay))
        return self.result


def test_main_without_a_profile_calls_analyze_with_just_the_query(capsys):
    stub = StubOrchestrator(ok_result())
    assert main(["sbin"], factory=lambda env_file: stub) == 0 and stub.queries == ["sbin"]


def test_main_passes_a_profile_holdings_and_an_amount_on(capsys, tmp_path):
    path = tmp_path / "h.csv"
    path.write_text("symbol,value\nSBIN,1000\n", encoding="utf-8")
    stub = OverlayStub(ok_result())
    argv = ["sbin", "--profile", "conservative", "--holdings", str(path), "--amount", "2500"]
    assert main(argv, factory=lambda env_file: stub) == 0
    ((query, overlay),) = stub.calls
    assert query == "sbin" and overlay.profile.name == "conservative" and overlay.amount == 2500.0
    assert [(h.symbol, h.value) for h in overlay.holdings] == [("SBIN", 1000.0)]


def test_main_reports_a_bad_profile_holdings_or_amount_without_a_traceback(capsys, tmp_path):
    stub = OverlayStub(ok_result())
    assert main(["sbin", "--holdings", "h.csv"], factory=lambda env_file: stub) == 1
    assert "error: --holdings and --amount need --profile" in capsys.readouterr().out
    assert main(["sbin", "--profile", "reckless"], factory=lambda env_file: stub) == 1
    assert "is not a preset" in capsys.readouterr().out
    assert main(["sbin", "--profile", "moderate", "--holdings", str(tmp_path / "none.csv")], factory=lambda env_file: stub) == 1
    assert "holdings: cannot read" in capsys.readouterr().out
    assert main(["sbin", "--profile", "moderate", "--amount", "-5"], factory=lambda env_file: stub) == 1
    assert "--amount must be a number above zero" in capsys.readouterr().out
    assert stub.calls == []


def test_offline_end_to_end_a_purchase_too_big_for_the_traded_value_is_held_back_and_printed(capsys):
    orchestrator = build_orchestrator(make_resolver(), FakeLLMRouter(), FakeChain(), clock=lambda: NOW)
    assert main(["sbin", "--profile", "moderate", "--amount", "5000000"], factory=lambda env_file: orchestrator) == 0
    out = capsys.readouterr().out
    assert "Verdict: Hold" in out and "[held back from Buy by your risk limits]" in out
    assert "[BREACH] Amount against daily traded value" in out and "[not checked] Position size not checked: no holdings were given." in out
    assert "risk profile 'moderate' applied" in out
