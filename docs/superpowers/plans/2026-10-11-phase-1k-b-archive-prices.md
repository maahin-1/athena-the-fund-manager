# Phase 1k-b: raw prices from NSE's daily archive files

**Goal:** a price source that gives the exchange's own printed (unadjusted) daily bars without the throttled website API, so the point-in-time test can run when `jugaad` is refused.

**Evidence (probed 2026-10-11):**
- `nsearchives.nseindia.com` answers while `www.nseindia.com` refuses (403).
- Old file `content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip`: columns `SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,...,ISIN`. Present to 2024-07-05, 404 from 2024-07-08.
- New file `content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`: columns `TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,ISIN`. Present from at least 2024-06-28; 404 at 2023-06-30.
- Weekends and holidays answer 404 (a 3.4 KB page).
- Prices are as printed that day: TCS shows 4268.50 on 2024-09-30 and 2473.90 on 2026-04-30.

**Design:** one file holds every symbol, so each day is fetched once and cached under `.athena/bhavcopy/`, whatever stocks are asked for.
- `athena/pit/bhavcopy.py`: `parse_bhavcopy` (either layout, one equity-series row per symbol, EQ first), `ArchiveClient` (spaced 1.5 s, 30 s / 90 s backoff on 403, 429, 503, 404 is "no file"), `BhavcopyStore` (disk cache; an empty file marks a day with no session), `BhavcopyPriceAdapter` (same `fetch_ohlcv` shape as `JugaadPriceAdapter`, source name `bhavcopy`).
- A 404 on a day the calendar calls a trading day is an error, not a gap. Without a calendar a 404 is read as a non-trading day.
- `pit_cases.UNADJUSTED_SOURCE` becomes `UNADJUSTED_SOURCES = {"jugaad", "bhavcopy"}`.
- `pit_run.live_builder` puts the archive adapter first, then jugaad, then Yahoo.
- The results list in `NseResults.filings` is cached on disk (`.athena/xbrl/index_<symbol>.json`) so it is fetched once per stock.

**Not done:** a ticker renamed since the date is looked up under the name it traded under then (the file has no cross-reference); the existing rename follower handles live use.

## Tasks
1. Parser, client, store, adapter with tests (`tests/test_pit_bhavcopy.py`).
2. Source set in `pit_cases`, wiring in `pit_run.live_builder`, filings list cache in `nse_results` with tests.
3. Real check: TCS closes for a past window against jugaad's cached bars and against the archive; then update the TRD and graph.
