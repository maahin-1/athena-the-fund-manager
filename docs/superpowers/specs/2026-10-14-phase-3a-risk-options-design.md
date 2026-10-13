# Phase 3a: risk options for backtested strategies

Parent: TRD 2.10 (the in-house backtest engine and the strategy format). Today a strategy has a buy condition, a sell condition and one optional protective stop; the engine is long-only, one position, all in.

## Goal

Let a strategy, whether built in the form or pasted as JSON, say how it manages risk beyond the fixed stop: a trailing stop, a profit target, a time limit, and how much cash goes into each entry.

## The JSON (version 1, all keys optional, nothing existing changes)

| Key | Meaning | Limits |
|---|---|---|
| `stop_atr` | (existing) stop this many ATRs below the entry fill | above 0, at most 10 |
| `trailing_stop_atr` | a stop this many ATRs (ATR at the entry decision) below the highest high since entry; it only ever rises | above 0, at most 10 |
| `take_profit_pct` | sell at this percentage above the entry fill | above 0, at most 1000 |
| `max_hold_bars` | after this many daily closes held, sell at the next open | whole number, 1 to 1000 |
| `position_pct` | share of cash put into each entry; the rest stays cash | above 0, at most 100 (default 100) |

A value of the wrong type, out of range, or a boolean, is refused with the path of the wrong part, as for the existing stop.

## Engine rules

1. Decisions still use only bars up to a close and fill at the next open; none of the new options looks ahead. The trailing level moves only after a bar has closed, so it applies from the next bar.
2. On each bar while holding, the highest of the fixed stop and the trailing level is the stop. If the bar opens at or below it the fill is at the open (a gap); otherwise if its low reaches it the fill is at the stop. Reason `stop` or `trailing_stop`.
3. If no stop was hit, the target fills at the open when the bar opens at or above it, otherwise at the target when the high reaches it. Reason `target`. If one bar could have hit both, the stop wins (the pessimistic reading).
4. After `max_hold_bars` closes held, a sell is scheduled for the next open unless one already is. Reason `time`. The bar the position was bought on counts as the first.
5. `position_pct` below 100 invests that share of cash; costs, the trade's return and the stops apply to the invested amount; the rest of the cash earns nothing, as before. The end-of-run value counts the cash that was never invested.
6. A strategy without these keys runs bit for bit as before (the engine's own tests keep their numbers).
7. The run report names each option the strategy used, the plain-English description includes them, and the blinding check still compares trades and reasons.

## Not in 3a

Shorting, pyramiding or several positions, volatility-targeted sizing, an intraday order of events inside a bar beyond rules 2 and 3, and risk limits across a portfolio.
