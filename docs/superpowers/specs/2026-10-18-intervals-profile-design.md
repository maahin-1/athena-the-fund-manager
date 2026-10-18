# Custom intervals and the bar volume profile (5b, second part)

Date 2026-10-18. Spec for the two items of 5b that need only bars: any interval a person types (7 minutes, 2 hours, 3 days, 2 weeks, 3 months), and a volume profile drawn beside the price from the bars in view. Tick recording and tick-based order flow wait for a market session (decision D4) and are not part of this.

## 1. Custom intervals

**What a person gets.** A box beside the interval buttons. Typing `7m`, `90`, `2h`, `3D`, `2W` or `3M` and pressing Enter shows that interval for the chart's symbol. The interval becomes a button (the last three typed are kept in the browser), and a built-in interval typed in any spelling (`60m`, `1d`) selects the built-in button. A wrong entry is refused with the reason and the limit.

**Where the bars come from.** No new server code: a custom interval is built in the page from a finer interval the server already serves, as 2m, 3m and 10m already are.

| Entry | Built from | How far back (the free source's depth) |
|---|---|---|
| minutes not a multiple of 5 | 1-minute bars | 5 days |
| multiple of 5, not of 15 | 5-minute bars | about a month |
| multiple of 15, not of 30 | 15-minute bars | about a month |
| multiple of 30, not of 60 | 30-minute bars | about a month |
| multiple of 60 (hours) | hourly bars | a year |
| days, weeks, months | daily bars | all of the stored history |

The depth is shown as the range buttons (a 7-minute chart offers 1D and 5D, a 2-hour chart 1M, 6M and 1Y), never as an empty chart.

**Limits.** Minutes 1 to 375 (a whole session), hours 1 to 6, days 2 to 60, weeks 2 to 26, months 2 to 24. A value the built-in buttons already offer (1m, 5m, 15m, 30m, 1h, 1D, 1W, 1M, and the derived 2m, 3m, 10m) is that built-in.

**How bars are grouped.** The grouping must not depend on how much history was loaded, or the same bar would change when a range changes.
- Minutes and hours: aligned to the 09:15 IST open, as the built-in derived intervals are (a 90-minute bar starts 09:15, 10:45, 12:15, 13:45 and 15:15; the last bar of a day is shorter, here 15 minutes, and is kept).
- Days: trading days are numbered as weekdays since 1970-01-01 (a holiday is still a number, so it makes a bar shorter, not a later start), and a bar holds `n` consecutive numbers. It is stamped with the date of its first weekday.
- Weeks: Monday-based weeks since 1970-01-01, `n` to a bar, stamped with the Monday of the first.
- Months: calendar months since 1970-01, `n` to a bar, stamped with the 1st of the first.

A live tick joins the bar that `barTime` gives its moment, and `barTime` and the grouping above are the same function, so a bar built from history and one grown from ticks cannot disagree (a test feeds every source bar through both and compares).

**Honest limits.** Weekly and monthly groups of weeks and months start at fixed points since 1970, not at a quarter or a financial-year start; a TradingView chart of 3 months may group from other months. A weekday that is a market holiday makes a day-group one bar short, and a special Saturday session joins Friday's group.

## 2. Bar volume profile

**What a person gets.** A toggle beside the chart type, and a row count (12, 24, 48, 96). When on, the price pane shows a histogram of volume by price along the right edge for the bars now in view (it follows scrolling and zooming), each row split into the volume of rising bars and falling bars, the row with the most volume (point of control) marked with a line, and the rows holding 70% of the volume around it (value area) drawn stronger than the rest. The data window gives the point of control and the value-area limits. Off by default; kept with the chart.

**How it is worked out.** Each bar's volume is spread evenly over the price rows its range (low to high) covers, in proportion to the overlap (a bar whose high equals its low goes to one row); it counts as rising when its close is at or above its open. The value area starts at the point of control and takes the larger of the next two rows above or below until it holds 70% of the volume. The total over the rows always equals the total volume of the bars (a test requires it).

**Honest limit, stated where it is drawn:** a bar does not say at which price its volume traded, so spreading it evenly over the bar's range is an approximation, finer for finer intervals. A profile from recorded ticks replaces it once ticks exist (tick recording waits for a market session). An index has no volume: nothing is drawn and the data window says so. It is off for the price-move chart types (Renko, Kagi and the like), whose bars are not times.

## 3. Out of scope

Tick recording, order flow and delta (wait for a session); a session or fixed-range profile (a profile of a chosen time span, with the drawing tools); intervals under a minute and tick charts (no source).

## 4. Testing

Pure units: interval parsing (every accepted spelling, every refusal with its limit), the source choice, grouping for each unit on hand-made series (including a holiday, a short last bar and a month boundary), the live-tick equals history property, profile row totals, point of control, value area, zero-volume and degenerate bars. End to end: type a custom interval and see the header, the buttons and the ranges; refuse a wrong one with its reason; turn the profile on and see its point of control in the data window.
