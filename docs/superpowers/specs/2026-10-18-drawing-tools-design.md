# Drawing tools (5d, first slice) — design

Date: 2026-10-18. Batch B4 in the master design. Requirements source: `2026-10-17-tradingview-breakdown.md` section 4.3 (drawing tools) and the 5d row of its roadmap: "engine (hit-testing, handles, snapping, persistence per symbol), then tools in tiers; object tree; lock, hide, sync".

## 1. What exists

The Chart Lab (`chartlab/`) draws one chart with Lightweight Charts v5 behind a `ChartRenderer` interface (`render.ts`); the view (`view.ts`) owns the toolbar, legends, data window and undo of chart state. Nothing can be drawn on the chart.

## 2. Goal

Draw on the Chart Lab chart, edit, lock, hide and delete drawings, with the drawings kept per symbol and still there next visit. First slice: the engine plus the commonly used tools. The rest of TradingView's 110 tools (pitchforks, Gann, patterns, Elliott, cycles, brushes, images) are later tiers on the same engine and are listed in section 11 so nothing is silently dropped.

Out of scope here: drawing alerts (5g needs the alert engine), syncing drawings across charts (one Chart Lab chart exists), visibility by timeframe, emoji/image tools, a "demonstration/magic" cursor.

## 3. Tools in this slice (22)

| Group | Tools | Anchors |
|---|---|---|
| Lines | Trend line, Ray, Extended line, Info line (trend line with price, % and bar count shown), Horizontal line, Horizontal ray, Vertical line, Cross line | 2, 2, 2, 2, 1, 1, 1, 1 |
| Channels | Parallel channel, Flat top/bottom | 3, 3 |
| Fibonacci | Retracement, Trend-based extension | 2, 3 |
| Shapes | Rectangle, Ellipse, Triangle | 2, 2, 3 |
| Annotation | Text, Price label | 1, 1 |
| Measure | Measure (price change, %, bars, time), Price range, Date range | 2, 2, 2 |
| Position | Long position, Short position (entry, stop, target, risk/reward) | 3 |

Flat top/bottom: a trend segment plus a horizontal line through the third anchor's price. Parallel channel: the third anchor's price sets the offset of the parallel line. Position tools take two clicks (entry, target) and put the stop at the mirror price; the stop handle is then dragged.

## 4. Data model

```
Anchor  { t: number, p: number }   // t = a bar's unix seconds (the bars' own `t`), p = price
Drawing { id, tool, points: Anchor[], style: { color: "#rrggbb", width: 1..4, dash: "solid"|"dashed"|"dotted", extendLeft?, extendRight?, fill? },
          text?: string (<= 200 chars), locked: bool, hidden: bool }
Doc     { version: 1, symbol, drawings: Drawing[] }   // order = draw order, last on top
```
Anchors are bar times, so the same drawing lands on the right bar at any interval (daily, weekly, 15-minute): its x is found by interpolating between the neighbouring bars' times. Beyond the last bar (or before the first) the position is extrapolated by whole bar steps (`stepSeconds`), so a ray or a forecast box can run into the future. Overnight and weekend gaps on intraday charts make that extrapolation approximate; it is stated in the notes.

Validation on load: known tool, right anchor count, finite numbers, colour `#rrggbb`, text length, at most 500 drawings per symbol; a bad document is refused and the old one kept.

## 5. Persistence

Server: `GET /api/drawings/:symbol` (204 when none), `PUT /api/drawings/:symbol` (object up to 200 KB), through the existing `LayoutStore` in a `drawings` folder next to `lists`. The store's file names allow letters, digits, space, `-`, `_`, so the symbol is encoded into the name: letters, digits, space and `-` as they are, every other character (including `_`) as `_` plus two hex digits (`M&M` is `M_26M`). Symbols are the page's own set (`A-Z 0-9 & ^ space -`, at most 24 characters). The page keeps a copy in local storage, saves 400 ms after the last change, and re-reads on symbol change. Server unreachable: the local copy is used and saved when the server returns.

## 6. Engine (pure TypeScript, no canvas)

- **Projection** (`drawings/projection.ts`): bars' times and bar step, plus two functions from the chart (price to y, and x from a fractional bar index); gives `x(t)`, `t(x)` (snapped to a bar or a whole step beyond), `y(p)`, `price(y)`. Tests use a linear stand-in.
- **Geometry** (`drawings/geometry.ts`): `shapesOf(drawing, projection, bounds)` returns lines, polylines, rectangles, ellipses, text and fills in pixels, clipped to the pane; handles come from `handlesOf`. `hit(drawing, projection, x, y)` returns `{ kind: "handle", index } | { kind: "body" } | null` with a 6 px tolerance; handles win over the body, the topmost drawing wins.
- **Fibonacci:** retracement from anchor A to anchor B with ratios 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1: level price = B + (A − B) × ratio, so 0 is at B (where the second click was) and 1 at A, as TradingView draws it. Trend-based extension from anchors A, B, C with ratios 0, 0.618, 1, 1.272, 1.618, 2.618: level price = C + (B − A) × ratio. Each level shows its ratio and its price.
- **Measure:** price change, percent, bars (count of bars between the two times, using the bars shown), and time span, in a label that is green for up, red for down.
- **Position:** risk = |entry − stop|, reward = |target − entry|, ratio reward ÷ risk to one decimal; shaded boxes for reward (green) and risk (red).
- **Magnet** (`drawings/magnet.ts`): off; weak = a point within 12 px of the open, high, low or close of the bar under the pointer snaps to it; strong = always the nearest of the four. Time always snaps to the nearest bar.
- **Creator** (`drawings/creator.ts`): a state machine over pointer events: `begin(tool)`, `click(x, y)`, `move(x, y)`, `cancel()`, returns the drawing in progress (with the cursor as its last anchor) and the finished drawing when the last anchor is placed. Escape cancels; double-click or Enter finishes polylines (none in this slice).
- **Editing** (`drawings/model.ts`): `moveBy(drawing, dt, dp)` (whole drawing), `moveHandle(drawing, index, anchor)`, add, remove, duplicate (offset by 10 px worth), reorder (front/back), lock, hide, style change, all returning a new list and leaving the old one alone, so undo is a stack of lists.

## 7. Drawing on the chart

A series primitive (`drawings/primitive.ts`, Lightweight Charts v5 `ISeriesPrimitive`) attached to the main series paints the shapes inside the pane in the chart's own paint cycle, so drawings stay in step with scrolling, zooming, log and percent scales and resizes with no timers. Selected drawings show square handles. The primitive gets the projection from the chart's time scale and the series' price scale at paint time.

Pointer events are taken in the capture phase on the chart host. With a tool chosen, clicks go to the creator and never reach the chart. With the cursor, a press on a drawing selects it and starts a move or handle drag (the chart's own pan and zoom are switched off for the drag); a press elsewhere deselects and the chart pans as before. The cursor becomes a pointer over a drawing and a crosshair while a tool is chosen. Keys on the chart: Delete removes the selected drawing, Escape cancels creation or deselects, Ctrl+Z / Ctrl+Y undo and redo (drawing actions and chart-state actions share one undo order).

Not drawn on Renko, Line break and Range bars (their x axis is not time): a note says so and the rail is disabled. Heikin Ashi and the other time-based types are fine.

## 8. Interface

- **Rail** (left of the chart, 40 px, scrolls when short): Cursor; one button per group that opens a flyout of its tools (the chosen tool shows on the button); Magnet (off, weak, strong); Keep drawing (the tool stays after a drawing); Lock all; Hide all; Remove all (asks, shows the count).
- **Properties bar** (over the top of the chart when a drawing is selected): colour, width, line style, extend left/right (for lines and channels), text (for text and labels), lock, duplicate, front/back, delete.
- **Objects tab** (the right panel gets tabs Data and Objects): every drawing and its tool, the first anchor's price and date, with show/hide, lock, delete, and click to select; indicators stay in the legends.
- Narrow window (under 900 px, where the data window is already hidden): the rail stays, the Objects tab is not shown.

## 9. Errors and edge cases

A click outside the pane (on an axis) is ignored. A drawing whose anchors are all off screen is simply not visible (the Objects tab can select it; "Fit to drawings" is not in this slice). Two anchors on the same bar and price make a zero-length line: kept, one handle, not hit-testable by body. Locked drawings select but do not move or take handle drags; Remove all skips none (locked ones are removed when asked, after the count is shown). A symbol with no drawings has an empty list, nothing is written. Loading a doc for another symbol while a drag is in progress drops the drag.

## 10. Testing

- Vitest, pure: projection both ways including extrapolation and a different interval; each tool's shapes for a known projection; hit-testing (handle over body, tolerance edge, topmost); fib levels and prices; measure and position numbers; magnet weak/strong thresholds; creator for every anchor count including Escape; model operations leave the old list untouched; validation of good and bad documents; store debounce, local fallback, symbol key encoding (`M&M`, `NIFTY 50`, `A_B`); the action log's undo order across chart and drawing actions.
- Rust: key encoding, GET 204/200, PUT round trip, size limit, bad symbol 400.
- Playwright (real warehouse): draw a trend line with two clicks and see it in the Objects tab and as pixels of its colour on the chart canvas; move it by dragging and see the anchors change; change colour and width; lock stops a drag; hide removes its pixels; Fibonacci and rectangle; reload keeps them; another symbol shows none and returning shows them; remove all; Renko disables the rail; no console errors; the layout audit still passes.

## 11. Later tiers on the same engine (not dropped)

Tier 2: pitchforks (classic, Schiff, modified Schiff, inside), more Fibonacci (channel, time zones, fan, circles, arcs, spiral, speed resistance), Gann box/fan/square, regression trend channel, disjoint channel, trend angle, rotated rectangle, circle, arc, curve, polyline/path, arrows, brush, highlighter, notes, callouts, signposts. Tier 3: patterns (XABCD, Cypher, head and shoulders, ABCD, three drives, Elliott), cycles and sine line, ghost feed, bars pattern, anchored VWAP and volume profile drawings, forecast tools. Drawing alerts with 5g.
