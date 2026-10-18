# Chart grid (5k)

Date 2026-10-19. Several Chart Lab charts on one page, with the symbol, interval, crosshair and time range kept in step when the person wants. Built from the existing Chart Lab view, not a second chart engine.

## 1. What a person gets

A **Grid** tab beside Chart Lab. A layout choice (2 across, 2 stacked, 3 across, 2 by 2, one large and two small) and four switches: **Symbol**, **Interval**, **Crosshair**, **Time**. Each cell is a chart with its own symbol box, interval buttons and custom interval box, chart type and indicators menu, legends, and live prices. Settings the person made in a cell (chart type, indicators, scale) are that cell's own.

| Switch | When on |
|---|---|
| Symbol | A symbol picked in any cell is shown in every cell. |
| Interval | An interval chosen in any cell (button or typed) is chosen in every cell. |
| Crosshair | Moving the pointer over a cell puts a crosshair on the same moment in the others. |
| Time | Scrolling or zooming a cell shows the same period in the others. |

Defaults: Symbol on, Crosshair on, Interval off, Time off. A new grid shows the same symbol in every cell at 1D, 1W, 1h and 15m (cells 1 to 4), the usual several-timeframes view, with the symbol of the other pages' link group (as the Chart Lab does). With fewer cells than intervals the first few are used.

What is kept in the browser: the layout, the four switches, and each cell's symbol and interval; each cell's own chart state under its own key. A damaged or foreign saved value is ignored and the defaults used.

## 2. Moments, not bar numbers

Cells may show different intervals, so the link between cells is a moment (unix seconds):
- an intraday bar's moment is its start; a daily, weekly or monthly bar's moment is the end of its first trading day (its date at 15:30 IST);
- the cell that receives a moment shows the last bar that began at or before it (an intraday cell compares the bar's start with the moment; a daily-based cell compares dates in IST);
- a moment before the cell's first bar, or after its last, shows nothing (the crosshair is cleared) rather than the nearest bar;
- a time range is two moments and is found the same way; a cell that has no bar in the period is left as it is.

A cell only sends what the pointer is doing in it (crosshair, scroll, zoom): a cell that received a moment does not send it on, so there is no loop. Price-move chart types (Renko, Kagi and the like) have no times and neither send nor receive crosshair and range.

## 3. Reuse and the changes it needs

`ChartLabView` takes a `cell` option: an id and callbacks for what the cell sends (symbol, interval, crosshair moment, time range). In that mode it stores its chart state under its own key, asks the live feed under its own name, does not follow the shared link group, hides its side panel, drawing rail, templates, compare, scale, ADJ, undo and snapshot controls, and does not take the page's keyboard shortcuts. It gets `applySymbol`, `applyInterval`, `showMoment` and `showPeriod` to receive. The chart renderer gets four optional members for the crosshair and the visible range. A single Chart Lab page is unchanged.

## 4. Limits

At most four cells. No per-cell drawings or compare in a cell (use the Chart Lab); no cell-level data window (the legend shows the values at the crosshair). Moments between cells of daily and intraday intervals match to the day, not the minute. A layout change keeps the cells it still has and drops or adds the rest, with new cells using the symbol and the next default interval.

## 5. Testing

Pure: layouts, state validation, moment of a bar, bar at a moment (each interval pair, before the first bar, after the last, a holiday), period to bar range, and the routing of the switches between fake cells (symbol, interval, crosshair, time; origin never receives; off switches send nothing; no loop). Page: a stub renderer cell sends and receives. End to end: the tab opens with four charts at 1D, 1W, 1h, 15m; the Interval switch changes all; the Symbol switch changes all; a layout change keeps symbols; a reload keeps layout and switches; Crosshair shows a matching crosshair readout in the other cells.
