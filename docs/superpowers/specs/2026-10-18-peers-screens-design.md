# Peers and screens (6f, 6g, 2026-10-18)

## Goal

A company's peers on its Fundamentals page, and a Screen tab that takes a typed query, saves screens, and filters by industry and index. Both work for every stock the warehouse screens (2,396 stocks), from stored data, with no per-company handling.

## Data

The stored screen row has a coarse `sector` (11 Yahoo-style sectors: 495 stocks are "Industrials") and no industry or index membership. Two things are added, both from the exchange's own files:

- **`index_members`** (warehouse table): `index_name, symbol, industry, fetched_at`, built by `athena.warehouse.members` from NSE's index constituent files. A file is found from an index name the warehouse already holds in `index_bars` (`ind_` + lower-case letters and digits of the name + `list.csv`, tested 2026-10-18: 38 of 179 indices have one, covering every broad, sector and thematic equity index; the rest are debt, futures and strategy indices, which have no constituent file at that name). Names with no file are skipped and counted; nothing is hand-listed. The `industry` column is NSE's own, one value per symbol (the most common across files). Refreshed by a `members` step of the nightly build and by hand (`python -m athena.warehouse.members`); a failed fetch keeps the last table.
- **`/api/screen`** returns two more columns: `industry` and `indices` (index names joined by `|`), empty when the table is missing.

## Peers (6f)

Computed on the page from the screen rows it already holds, by a pure function (`peers.ts`), so it is tested without a server.

- **Peer set:** stocks in the same industry when the subject's industry is known and at least four other stocks share it, else the same sector; only stocks with a market cap; ranked by closeness in market cap (distance of the logarithms); twelve by default, thirty at most. The basis (industry or sector) is shown.
- **Page:** a Peers section on the Fundamentals company page after the key figures: breadcrumb (sector, industry), the indices the stock belongs to, a table of the subject and its peers with a median row (price, market cap, P/E, P/B, ROE, net margin, debt to equity, dividend yield, sales growth, 1-year return; columns editable from the screen's full column list), and an "Against peers" table: for each figure the subject's value, the peers' median, and where the subject ranks among peers (percentile), with the direction that is better written next to it. A peer's symbol opens its page.
- **Honest limits:** peers are by industry (NSE's classification, known for stocks in the indices above) or coarse sector and size, not by business model; figures come from the stored screen (Yahoo-based fundamentals), not yet from the filing store (see Later).

## Screens (6g)

- **Query language** (`screenquery.ts`, pure): `pe < 20 and roe > 15% and (sector = "Technology" or industry = "Financial Services") and index = "Nifty 500"`. Comparisons `< <= > >= = !=`, `between a and b`, `in (...)`, `is null`, `is not null`, `and`, `or`, `not`, parentheses; numbers, percents (`15%` is 0.15, since ratios are stored as fractions), quoted text. A field catalogue gives each field's names, label, unit and an example; field and value names are case-insensitive. A missing figure meets no limit (as the filters do today). An error says what and where. `index` is a set: `=` and `in` mean "is a member".
- **Saved screens:** one document on the server (`/api/screens`, as watchlists are): name, query, filters, columns, sort. Up to 100, names up to 60 characters. Kept across browsers and layouts.
- **Screen tab:** the query box (Enter runs it, the error shows under it), a field list, Save / Load / Delete, an industry filter and an in-index filter beside the sector filter, the presets (each shows its equivalent query), column choice, sorting, CSV export of the result. The query and the filters both apply (and).

## Later (not in this slice)

Fields from the filing store (ROCE, 3 and 5 year growth, interest cover, promoter holding) join the screen as the full fetch finishes: a `fund_metrics` table built from the ratio engine for every company with stored statements, read through the same field catalogue. Phrase queries in natural language belong to the AI layer (6i). Screen alerts belong to 5g.

## Evidence required

Python: the file discovery, the CSV parse, the build over a temp warehouse, a failed fetch keeping the table. Rust: the two extra columns, `/api/screens` save, load and validation. TypeScript: peer choice (basis, ranking, limit, medians, percentiles, missing figures), the query language (every operator, precedence, percent, errors with positions, case, sets, missing values), saved-screen validation. Browser: a query filters the table, an error shows, a screen saves and comes back after reload, peers appear for several stocks (a bank, an IT company, a renamed one) with the subject first. Then the full suites.
