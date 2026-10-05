import json
from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live

LOOKBACK_DAYS = 760  # about 25 months, enough for the monthly trend


class Narrator:
    """A scripted model that cites real figures from whatever packet it receives."""

    def complete(self, system, user):
        data = json.loads(user[user.index("{") : user.rindex("}") + 1])["metrics"]
        text = f"Last close {data['last_close']['value']} with RSI {data['rsi_14']['value']} and daily trend {data['trend_daily']['value']}."
        return json.dumps({"signal": "neutral", "confidence": 50, "reasoning": text})


@pytest.mark.parametrize("symbol", ["SBIN", "NIFTYBEES"])
def test_live_technical_packet_is_complete_and_the_specialist_accepts_it(symbol):
    since = ist_date(utc_now()) - timedelta(days=LOOKBACK_DAYS)
    bars = JugaadPriceAdapter().fetch_ohlcv(symbol, "1d", since=since)
    packet = build_technical_packet(symbol, utc_now(), bars)
    print("\n" + json.dumps({k: v["value"] for k, v in packet["metrics"].items()}, indent=1), packet["missing_reasons"])

    assert packet["missing"] == [], packet["missing_reasons"]
    assert 0 <= packet["metrics"]["rsi_14"]["value"] <= 100
    assert packet["metrics"]["trend_alignment"]["value"] in ("aligned_up", "aligned_down", "not_aligned")
    assert packet["metrics"]["volume_ratio_20_50"]["value"] > 0

    out = Specialist(QUANT_TECHNICAL, Narrator()).analyze(packet)
    assert out["data_coverage"] == "full" and out["missing"] == []
