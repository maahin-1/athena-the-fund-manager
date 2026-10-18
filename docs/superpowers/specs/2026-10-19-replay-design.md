# Bar replay (5j)

Date 2026-10-19. Replay a chart's history one bar at a time from a chosen bar, so a person can look at a stock as it looked then and judge a setup without knowing what came next. Paper trading with virtual money is parked by the user and is not part of this.

## 1. What a person gets

A **Replay** button in the Chart Lab toolbar.

1. Click it, then click the bar to start from (a hint says so; Esc cancels). The chart shows the history up to that bar, as if it were the latest.
2. A replay bar replaces the live feel: **Back**, **Play / Pause**, **Next**, a **speed** (nine, from one bar every 10 seconds to 50 bars a second), **Jump to…** (type a date), and **Exit**. The keys Right, Left and Space do the same when no box is focused.
3. Each step reveals one more bar. Indicators, the volume profile, the legend values, the data window and drawings all work on what has been revealed and nothing else, so nothing can look ahead.
4. Playing stops at the last bar with a message. Exit returns to the full chart and live prices.
5. Changing the interval in replay keeps the moment: the chart shows the same time at the new interval (the bars that had completed by then). Changing the symbol exits replay.

While replaying, live prices do not move the chart (they would reveal the future).

## 2. The revealed bars and look-ahead

Replay is defined by a moment `M`, the end of the last revealed bar of the chart that is driving it. A chart shows a bar only when the bar has **ended** by `M`:
- an intraday bar ends at its start plus its length;
- a daily bar ends at 15:30 IST of its date;
- a bar of several days, a week or a month ends at the start of the next bar (or its own length after its start, when it is the last), so it appears when the next one begins, never before it is complete.

For the chart that is driving, stepping reveals the next bar and `M` becomes that bar's end. A coarser chart that is replaying at the same `M` (section 5) therefore shows through yesterday while an intraday chart is part-way through today: it never shows a daily bar that still has hours to run, because the page has no intraday data inside a daily bar to build it from.

## 3. Behaviour that must hold

- Revealed bars are a prefix of the loaded bars; the loaded bars are never changed.
- Stepping forward only adds a bar (cheap update, no rebuild); stepping back and jumping rebuild.
- A chart with no bar before the start moment shows nothing and says so.
- Price-move chart types (Renko and the like) are built from the revealed bars; their bars change as bars are added.
- Play uses a timer that is stopped when replay ends, the tab is left, or the page is hidden; a speed change takes effect on the next step.
- Replay is not kept between visits.
- Quotes, alerts and compare data are not revealed beyond `M`: the compare line is cut at the same bars.

## 4. Interaction with other parts

Drawings, the volume profile and indicators use the revealed bars through the model, with no change of their own. Custom intervals work (their bars are built from loaded finer bars before the cut, so a bar is complete only when its last source bar has been revealed). The adjusted/raw switch reloads and keeps the moment.

## 5. Synced replay in the Grid

A **Replay** button in the grid bar: start replay by clicking a bar in any chart (that chart drives and holds the controls); every chart then reveals the bars that have ended by `M`; Play, Next and Back in the driving chart move `M`, and the others follow by `M`. Stepping the driver back moves `M` back. A chart at a coarser interval than the driver moves only when its next bar completes. Exit in the driver, or the grid's Replay button again, exits all. A chart brought in by a larger layout joins at the current `M`.

## 6. Limits

Replay at the interval's own bars only (no tick or second-level replay: there is no source). An intraday chart reaches back as far as the free source holds (5 days of 1-minute bars, about a month of 5, 15 and 30-minute bars, a year of hourly bars). A symbol with splits shows adjusted prices as the chart does.

## 7. Testing

Pure: the end of a bar for each interval kind, bars revealed at a moment, the prefix count for a cursor, nearest bar for a date, speeds, the stepping state machine (forward, back, jump, reach the end, play and pause, speed change). Model: revealed bars and the indicators on them equal the same indicators computed on the truncated bars; stepping forward is a tick, stepping back is a rebuild; compare is cut. Page: pick a start with a click, the controls, keys, Escape, live prices ignored, interval change keeps the moment, symbol change exits. Grid: drivers and followers by moment, a coarser chart lagging, exit all. End to end: start from a bar, step, play at speed, jump to a date, change interval, exit; and synced replay in the grid.
