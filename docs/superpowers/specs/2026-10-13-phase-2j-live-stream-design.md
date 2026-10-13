# Phase 2j: a live price stream for the dashboard (Angel One)

Parent: the live price panel (TRD 2.20, "Live price panel"). The panel polls the exchange's own quote page, which changes only about every five seconds, so the price cannot move more often than that.

## Goal

Show every trade as it arrives for the symbol being viewed, by subscribing to Angel One's SmartAPI market-data WebSocket, while keeping the polled quote as the fallback.

## Rules

1. **Read-only.** The code logs in and subscribes to prices. It never calls an order, position or funds endpoint.
2. **Login without a browser.** The API key, client code, PIN and TOTP secret come from the settings file; the six-digit code is made from the secret at each login. None of them, and no token, is printed, logged or put in an error message or an object's text form.
3. **Which symbols.** Only the symbols being viewed, at most 3 at a time (the oldest is unsubscribed when a fourth is watched). Symbol to token comes from Angel One's instrument file (downloaded once a day, kept in `.athena/`); the cash series is found as `SYMBOL-EQ`, then `-BE`, `-BZ`, `-SM`, `-ST`.
4. **Quote from a tick.** Snap-quote mode gives last price, previous close, open, high, low, volume, average price, five levels of depth, 52-week range and circuit limits, with prices in paise. A tick becomes the same `Quote` the panel already draws, marked with its source.
5. **Fresh or fall back.** The panel uses the stream while its last tick for the symbol is at most 15 seconds old (a quiet stock may not trade that often, so the exchange's own timestamp, not arrival time, is not used for this); otherwise, or while connecting, or if the stream fails for any reason, it uses the polled exchange quote and says so. A broken stream is never an error on the page.
6. **Every tick on the chart.** Each distinct tick (price, exchange time) is kept so the chart line moves with every trade.
7. **Market hours.** Outside trading hours the stream delivers only a snapshot; the panel already says the market is closed.
8. **Not in the verdict.** Nothing from the stream reaches an analyst or a verdict.

## Not in 2j

Orders, the portfolio, historical candles from Angel One, subscribing to the whole market, and storing ticks.
