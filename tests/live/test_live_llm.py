import json
from collections import defaultdict
from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.evaluation.schema import validate_specialist_output
from athena.llm.probe import probe_models
from athena.llm.router import build_router
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live


def test_live_every_configured_provider_answers_with_at_least_one_model():
    results = probe_models()
    if not results:
        pytest.skip("no LLM provider key is set")
    by_provider = defaultdict(list)
    for r in results:
        by_provider[r.provider].append(r)
        print(f"\n{'OK  ' if r.ok else 'FAIL'} {r.provider:10s} {r.model:45s} {r.seconds:5.1f}s {r.detail}", end="")
    for provider, provider_results in by_provider.items():
        assert any(r.ok for r in provider_results), (provider, [r.detail for r in provider_results])


def test_live_quant_technical_specialist_runs_end_to_end_through_the_router():
    try:
        router = build_router()
    except AthenaError:
        pytest.skip("no LLM provider key is set")
    llm = router.client_for("specialist")
    since = ist_date(utc_now()) - timedelta(days=760)
    bars = JugaadPriceAdapter().fetch_ohlcv("SBIN", "1d", since=since)
    packet = build_technical_packet("SBIN", utc_now(), bars)
    assert packet["missing"] == [], packet["missing_reasons"]

    output = Specialist(QUANT_TECHNICAL, llm).analyze(packet)
    print(f"\nanswered by {llm.last_source}: {json.dumps(output)[:400]}")
    assert validate_specialist_output(output) == []
    assert output["data_coverage"] == "full" and output["missing"] == []
