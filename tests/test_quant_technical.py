import json
from datetime import datetime, timedelta, timezone

from athena.agents.base import Specialist
from athena.agents.quant_technical import PERSONA, QUANT_TECHNICAL
from athena.contracts import Bar
from athena.evaluation.checks import check_abstention
from athena.technicals.packet import build_technical_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def bars_for(closes):
    days, day = [], datetime(2026, 10, 2).date()
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar("X", datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1), c, c + 1, c - 1, c, 1000.0, NOW, "t")
        for d, c in zip(reversed(days), closes)
    ]


UP_PACKET = build_technical_packet("X", NOW, bars_for([100.0 + i * 0.5 for i in range(400)]))


class Narrator:
    """A fake model that reads the packet it is given and cites real figures from it."""

    def complete(self, system, user):
        data = json.loads(user[user.index("{") : user.rindex("}") + 1])
        metrics = data["metrics"]
        parts = [f"Daily trend {metrics['trend_daily']['value']}", f"RSI {metrics['rsi_14']['value']}"]
        if "trend_alignment" in metrics:
            parts.append(f"alignment {metrics['trend_alignment']['value']}")
        else:
            parts.append("timeframe alignment is unavailable so confidence is limited")
        return json.dumps({"signal": "bullish", "confidence": 65, "reasoning": ". ".join(parts) + "."})


def test_persona_encodes_the_trd_rules():
    for rule in ("aligned", "Bollinger", "Volume confirmation", "at least 2", "never as a price target"):
        assert rule in PERSONA


def test_full_data_gives_full_coverage_and_a_grounded_view():
    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(UP_PACKET)
    assert out["data_coverage"] == "full" and out["missing"] == []
    assert "alignment aligned_up" in out["reasoning"]


def test_short_history_gives_partial_coverage_naming_the_gaps():
    packet = build_technical_packet("X", NOW, bars_for([100.0 + i * 0.5 for i in range(100)]))
    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(packet)
    assert out["data_coverage"] == "partial"
    assert {"trend_monthly", "trend_alignment", "sma_200"} <= set(out["missing"])


def test_too_little_history_abstains():
    packet = build_technical_packet("X", NOW, bars_for([100.0 + i for i in range(10)]))
    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(packet)
    assert (out["signal"], out["confidence"], out["data_coverage"]) == ("neutral", 0, "insufficient")


def test_abstention_harness_passes_for_the_real_specialist():
    specialist = Specialist(QUANT_TECHNICAL, Narrator(), fail_quiet=True)

    def run(metrics):
        return specialist.analyze({**UP_PACKET, "metrics": metrics})

    failures = check_abstention(
        run, UP_PACKET["metrics"], critical=list(QUANT_TECHNICAL.critical), optional=list(QUANT_TECHNICAL.optional)
    )
    assert failures == []
