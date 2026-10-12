# Phase 2g: the Analyse tab reads the warehouse first

Parent: [market warehouse design](2026-10-12-market-warehouse-design.md). Every Analyse request still downloads prices (jugaad or Yahoo), the exchange's and Yahoo's corporate-action records, and the statements (Yahoo), which is slow and the part NSE blocks.

## Rules

1. **Prices.** When the warehouse is readable, its last loaded day is at most 5 days old, it covers the 760-day window the analysis asks for, and it holds bars for the symbol that end within 10 days of its last loaded day, the raw bars come from it. Otherwise the live chain is used, as now.
2. **Actions and splits.** When the bars came from the warehouse, its stored actions are used and Yahoo's split record is not asked for; when they did not, the live sources are used as now.
3. **Statements.** When the warehouse holds a payload for the symbol that is at most 30 days old, the fundamentals packet is built from it (its fetch time is the data's date); otherwise Yahoo is asked, as now.
4. **Say so.** The view carries a note naming the source and its date ("prices from the market database, last close 2026-10-09"; "statements fetched 2026-10-12"), so a stale or stored figure is never shown as live.
5. A warehouse that is missing, locked beyond a short wait, or fails to read is not an error: the live path runs and the note is absent.
6. The warehouse is opened read-only for each read and closed; nothing here writes it.

## Not in 2g

The long history for backtests (8 years; the warehouse holds 3.8), news, NSE filings checks for mergers, the index valuation history, and the risk panel's market series: all stay live.
