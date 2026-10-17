"""Reference vectors for the Chart Lab's indicator maths: 300 seeded daily bars and what TA-Lib computes on them.

    python scripts/make_indicator_vectors.py

Writes terminal/web/test/fixtures/indicator_vectors.json. The web tests compare the TypeScript maths against it element by element
(null where TA-Lib gives NaN). Rerun only when the set of indicators changes."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import talib

OUT = Path(__file__).resolve().parent.parent / "terminal" / "web" / "test" / "fixtures" / "indicator_vectors.json"


def clean(values: np.ndarray) -> list[float | None]:
    return [None if np.isnan(v) else round(float(v), 10) for v in values]


def main() -> None:
    rng = np.random.default_rng(20261017)
    n = 300
    close = 1000 + np.cumsum(rng.normal(0, 12, n))
    open_ = close + rng.normal(0, 4, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 6, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 6, n))
    volume = rng.integers(100_000, 900_000, n).astype(float)
    close, open_, high, low = (np.round(a, 4) for a in (close, open_, high, low))  # the stored bars are rounded, so TA-Lib sees the same numbers
    slowk, slowd = talib.STOCH(high, low, close, fastk_period=14, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    macd, macd_signal, macd_hist = talib.MACD(close, 12, 26, 9)
    upper, middle, lower = talib.BBANDS(close, 20, 2, 2, 0)
    aroon_down, aroon_up = talib.AROON(high, low, 14)
    expected = {
        "sma_20": clean(talib.SMA(close, 20)),
        "ema_20": clean(talib.EMA(close, 20)),
        "wma_10": clean(talib.WMA(close, 10)),
        "rsi_14": clean(talib.RSI(close, 14)),
        "macd": clean(macd), "macd_signal": clean(macd_signal), "macd_hist": clean(macd_hist),
        "bb_upper": clean(upper), "bb_mid": clean(middle), "bb_lower": clean(lower),
        "stoch_k": clean(slowk), "stoch_d": clean(slowd),
        "atr_14": clean(talib.ATR(high, low, close, 14)),
        "adx_14": clean(talib.ADX(high, low, close, 14)),
        "obv": clean(talib.OBV(close, volume)),
        "cci_20": clean(talib.CCI(high, low, close, 20)),
        "willr_14": clean(talib.WILLR(high, low, close, 14)),
        "roc_10": clean(talib.ROC(close, 10)),
        "mfi_14": clean(talib.MFI(high, low, close, volume, 14)),
        "trix_15": clean(talib.TRIX(close, 15)),
        "aroon_up": clean(aroon_up), "aroon_down": clean(aroon_down),
        "sar": clean(talib.SAR(high, low, 0.02, 0.2)),
    }
    bars = [{"t": 1_700_000_000 + 86_400 * i, "o": round(float(open_[i]), 4), "h": round(float(high[i]), 4), "l": round(float(low[i]), 4),
             "c": round(float(close[i]), 4), "v": float(volume[i])} for i in range(n)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"bars": bars, "expected": expected}), encoding="utf-8")
    print(f"wrote {OUT} ({n} bars, {len(expected)} series)")


if __name__ == "__main__":
    main()
