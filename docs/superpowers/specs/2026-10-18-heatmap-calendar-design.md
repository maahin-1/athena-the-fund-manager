# Heatmap and calendars (5i, 2026-10-18)

## Goal

A market heatmap and two calendars inside the Screen tab, for every stock the warehouse screens, from stored data. The heatmap respects the screen's filters and query, so "the heatmap of my saved screen" is one click.

## Heatmap

A treemap of the screen's current result. Pure layout (`treemap.ts`), painted as positioned tiles.

- **Tiles:** one per stock with a market cap (the 600 largest of the result), area in proportion to market cap (or to traded value, a choice), grouped by sector or industry or not at all; a group is drawn as a box with a label, its stocks tiled inside (squarified, so tiles stay near square).
- **Colour:** by a chosen figure on a diverging red to green scale with a fixed range per figure (1-day ±3%, 1-month ±10%, 3-month ±20%, 6-month ±30%, 1-year ±50%, distance from the 52-week high 0 to -40%, RSI 30 to 70); a stock with no figure is grey. The legend names the range.
- **Reading it:** the tile shows the symbol and the figure when it is big enough; a tooltip gives name, sector, industry, market cap and the figure; a click selects the stock and shows the same actions as the table (Chart, Research, Fundamentals, add to watchlist).
- **Not in this slice:** colouring by fundamentals not in the screen (they join with the filing-store fields), custom colour scales, a sector-level aggregate figure (a group's tile colour), animation.

## Calendars

- **Results and board meetings:** NSE's event calendar (`/api/event-calendar?index=equities`, needs no cookies) lists board meetings for the next four weeks with the purpose ("Financial Results", "Financial Results/Dividend", "Fund Raising"...) and the date: 437 entries on 2026-10-18. The warehouse keeps them in `events` (symbol, date, purpose, description, company), refreshed by an `events` step of the nightly build and `python -m athena.warehouse.events` by hand; rows of earlier dates stay, so the calendar grows a past as it is refreshed; a refresh in which nothing answers keeps the table.
- **Corporate actions:** the `actions` table the chart adjustment already reads (20,224 rows, ex-dates from 2018 to a year ahead): dividends, splits, bonuses, rights, buybacks, demergers and schemes by ex-date, classified from the exchange's own wording by a small function with tests.
- **API:** `GET /api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD` returns `{events, actions}` with the company name from the universe; at most 366 days, at most 5,000 rows each; bad dates are 400.
- **Page:** a Calendar mode in the Screen tab: a range (this week, next 2 weeks, next month, past month, or two dates), kind filters, a symbol or name search, and "only stocks in my screen" (the current filters and query), rows grouped by date, a symbol opening its stock. Upcoming results of stocks in the user's watchlist are marked.
- **Not built, and said so on the page:** an economic calendar (central bank decisions, inflation releases): no free structured source was found; earnings estimates and actuals; IPO dates.

## Evidence required

Treemap: areas proportional, no overlap, inside the box, groups inside the box, squarified aspect ratios, determinism, one item, zero values. Python: the event parse, the refresh over a temp warehouse (replacement window, kept past, failed fetch keeps the table). Rust: the action classifier over real wordings, the calendar route (range, kinds, limits, bad dates, missing tables). Browser: the heatmap draws tiles sized and coloured, regroups, follows a query, selects a stock; the calendar lists upcoming results from real data and filters. Then the full suites.
