//! Stored analysis for one symbol, read from the warehouse: the screen row, the technical and fundamentals packets and the latest verdict.
//! Everything here is stored data, so it works whether or not the market is open. The file is opened read-only for each request.
use std::path::Path;

use duckdb::{AccessMode, Config, Connection, OptionalExt};
use serde_json::{json, Map, Value};

#[derive(Debug, PartialEq, Eq)]
pub enum AnalysisError {
    Busy,
}

fn connect(path: &Path) -> Result<Connection, AnalysisError> {
    let config = Config::default().access_mode(AccessMode::ReadOnly).map_err(|_| AnalysisError::Busy)?;
    Connection::open_with_flags(path, config).map_err(|_| AnalysisError::Busy)
}

/// The columns of the `screen` table the page shows, in order.
const SCREEN_COLUMNS: [&str; 24] = [
    "name", "sector", "kind", "data_as_of", "close", "ret_1d", "ret_1m", "ret_3m", "ret_6m", "ret_1y", "from_52w_high", "from_52w_low",
    "traded_value_cr", "rsi_14", "trend", "sma50_vs_200", "market_cap_cr", "pe", "pb", "roe", "net_margin", "debt_to_equity",
    "dividend_yield", "revenue_growth",
];

fn screen_row(conn: &Connection, symbol: &str) -> Option<Value> {
    // every value is read as text and turned back into a number when it is one, so one query serves text and number columns alike
    let select: Vec<String> = SCREEN_COLUMNS.iter().map(|c| format!("CAST({c} AS VARCHAR)")).collect();
    let sql = format!("SELECT {} FROM screen WHERE symbol = ?", select.join(", "));
    conn.query_row(&sql, [symbol], |r| {
        let mut out = Map::new();
        for (i, name) in SCREEN_COLUMNS.iter().enumerate() {
            let text: Option<String> = r.get(i)?;
            let value = match text {
                None => Value::Null,
                Some(t) => t.parse::<f64>().ok().filter(|n| n.is_finite()).map(|n| json!(n)).unwrap_or(Value::String(t)),
            };
            out.insert((*name).to_string(), value);
        }
        Ok(Value::Object(out))
    })
    .optional()
    .ok()
    .flatten()
}

/// A packet's metrics as a list `{name, value, unit, window, note, group}`, with its flags and missing names.
fn packet(conn: &Connection, symbol: &str, kind: &str) -> Option<Value> {
    let (as_of, payload, stored_flags): (String, String, Option<String>) = conn
        .query_row("SELECT CAST(data_as_of AS VARCHAR), payload, flags FROM packets WHERE symbol = ? AND kind = ?", [symbol, kind], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })
        .optional()
        .ok()
        .flatten()?;
    let body: Value = serde_json::from_str(&payload).ok()?;
    // the build stores the warnings in the `flags` column (price breaks no record explains); the payload may carry its own
    let mut flags: Vec<Value> = stored_flags.and_then(|t| serde_json::from_str::<Vec<Value>>(&t).ok()).unwrap_or_default();
    for f in body.get("data_quality_flags").and_then(Value::as_array).cloned().unwrap_or_default() {
        if !flags.contains(&f) {
            flags.push(f);
        }
    }
    let metrics: Vec<Value> = body["metrics"]
        .as_object()
        .map(|m| {
            m.iter()
                .map(|(name, v)| json!({"name": name, "value": v["value"], "unit": v["unit"], "window": v["window"], "note": v["note"], "group": v["group"]}))
                .collect()
        })
        .unwrap_or_default();
    Some(json!({
        "as_of": as_of,
        "flags": flags,
        "missing": body.get("missing").cloned().unwrap_or_else(|| json!([])),
        "sector": body.get("sector").cloned().unwrap_or(Value::Null),
        "metrics": metrics,
    }))
}

fn latest_verdict(conn: &Connection, symbol: &str) -> Option<Value> {
    let (made_at, data_as_of, close, payload): (String, String, Option<f64>, String) = conn
        .query_row(
            "SELECT CAST(made_at AS VARCHAR), CAST(data_as_of AS VARCHAR), close, payload FROM verdicts WHERE symbol = ? ORDER BY made_at DESC LIMIT 1",
            [symbol],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .optional()
        .ok()
        .flatten()?;
    Some(json!({"made_at": made_at, "data_as_of": data_as_of, "close": close, "payload": serde_json::from_str::<Value>(&payload).ok()?}))
}

/// Everything stored about `symbol`; a symbol the warehouse does not know still answers, with nulls.
pub fn analysis(path: &Path, symbol: &str) -> Result<Value, AnalysisError> {
    let conn = connect(path)?;
    let last_day: Option<String> = conn.query_row("SELECT CAST(max(day) AS VARCHAR) FROM days WHERE status = 'loaded'", [], |r| r.get(0)).ok().flatten();
    let universe: Option<(String, String)> = conn
        .query_row("SELECT name, kind FROM universe WHERE symbol = ?", [symbol], |r| Ok((r.get(0)?, r.get(1)?)))
        .optional()
        .ok()
        .flatten();
    Ok(json!({
        "symbol": symbol,
        "name": universe.as_ref().map(|u| u.0.clone()),
        "kind": universe.as_ref().map(|u| u.1.clone()),
        "market_day": last_day,
        "screen": screen_row(&conn, symbol),
        "technical": packet(&conn, symbol, "technical"),
        "fundamentals": packet(&conn, symbol, "fundamentals"),
        "verdict": latest_verdict(&conn, symbol),
    }))
}

/// The columns `screen_all` returns, in order. These are text; every other column is a number (or empty). The last nine were added later: a warehouse built before them has none
/// of them, and each is then null for every row.
const SCREEN_ALL: [&str; 35] = [
    "symbol", "kind", "name", "sector", "close", "ret_1d", "ret_1m", "ret_3m", "ret_6m", "ret_1y", "from_52w_high", "from_52w_low", "traded_value_cr",
    "rsi_14", "trend", "sma50_vs_200", "market_cap_cr", "pe", "pb", "roe", "net_margin", "debt_to_equity", "dividend_yield", "earnings_yield",
    "revenue_growth", "flags", "industry_group", "pe_vs_industry", "pb_vs_industry", "roe_vs_industry", "value_base", "price_vs_value", "value_position",
    "implied_return", "valued_on",
];
const SCREEN_TEXT: [&str; 7] = ["symbol", "kind", "name", "sector", "trend", "industry_group", "valued_on"];

/// A two-column query as symbol -> text; empty when the query fails (a table that does not exist yet).
fn symbol_map(conn: &Connection, sql: &str) -> std::collections::HashMap<String, String> {
    let Ok(mut stmt) = conn.prepare(sql) else { return Default::default() };
    let Ok(rows) = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))) else { return Default::default() };
    rows.filter_map(Result::ok).collect()
}

/// Every row of the stored market screen (all stocks and ETFs) as `{columns, rows, market_day}`, for the page to filter and sort. A missing figure is `null`, never zero.
pub fn screen_all(path: &Path) -> Result<Value, AnalysisError> {
    let conn = connect(path)?;
    let held = symbol_map(&conn, "SELECT column_name, column_name FROM information_schema.columns WHERE table_name = 'screen'");
    let select: Vec<String> = SCREEN_ALL.iter().map(|c| if held.contains_key(*c) { format!("CAST({c} AS VARCHAR)") } else { "CAST(NULL AS VARCHAR)".to_string() }).collect();
    let mut stmt = conn.prepare(&format!("SELECT {} FROM screen ORDER BY traded_value_cr DESC NULLS LAST", select.join(", "))).map_err(|_| AnalysisError::Busy)?;
    let rows = stmt
        .query_map([], |r| {
            let mut out: Vec<Value> = Vec::with_capacity(SCREEN_ALL.len());
            for (i, name) in SCREEN_ALL.iter().enumerate() {
                let text: Option<String> = r.get(i)?;
                out.push(match text {
                    None => Value::Null,
                    Some(t) if SCREEN_TEXT.contains(name) => Value::String(t),
                    Some(t) => t.parse::<f64>().ok().filter(|n| n.is_finite()).map(|n| json!(n)).unwrap_or(Value::Null),
                });
            }
            Ok(Value::Array(out))
        })
        .map_err(|_| AnalysisError::Busy)?
        .filter_map(Result::ok)
        .collect::<Vec<_>>();
    // the exchange's industry and the indices each stock is in (the table is missing in a warehouse built before it existed: both columns are then null)
    let industries = symbol_map(
        &conn,
        "WITH size AS (SELECT index_name, count(*) AS n FROM index_members GROUP BY 1)          SELECT m.symbol, arg_max(m.industry, size.n) FROM index_members m JOIN size USING (index_name) WHERE m.industry <> '' GROUP BY m.symbol",
    );
    let indices = symbol_map(&conn, "SELECT symbol, string_agg(index_name, '|' ORDER BY index_name) FROM index_members GROUP BY symbol");
    let rows = rows
        .into_iter()
        .map(|mut row| {
            let symbol = row[0].as_str().unwrap_or_default().to_string();
            let extra = |map: &std::collections::HashMap<String, String>| map.get(&symbol).map(|t| Value::String(t.clone())).unwrap_or(Value::Null);
            if let Value::Array(cells) = &mut row {
                cells.push(extra(&industries));
                cells.push(extra(&indices));
            }
            row
        })
        .collect::<Vec<_>>();
    let mut columns: Vec<&str> = SCREEN_ALL.to_vec();
    columns.extend(["industry", "indices"]);
    let market_day: Option<String> = conn.query_row("SELECT CAST(max(day) AS VARCHAR) FROM days WHERE status = 'loaded'", [], |r| r.get(0)).ok().flatten();
    Ok(json!({"columns": columns, "rows": rows, "market_day": market_day}))
}

/// Whether the warehouse knows a symbol: a stock or ETF in the universe, or an index it holds bars for. Lets the page refuse a typo instead of waiting for a quote that never comes.
pub fn known(path: &Path, symbol: &str) -> Result<Value, AnalysisError> {
    let conn = connect(path)?;
    let listed: Option<(String, String, Option<String>)> = conn
        .query_row("SELECT name, kind, isin FROM universe WHERE symbol = ?", [symbol], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .optional()
        .map_err(|_| AnalysisError::Busy)?;
    if let Some((name, kind, isin)) = listed {
        return Ok(json!({"known": true, "name": name, "kind": kind, "isin": isin}));
    }
    let index: Option<String> = conn
        .query_row("SELECT index_name FROM index_bars WHERE lower(index_name) = lower(?) LIMIT 1", [symbol], |r| r.get(0))
        .optional()
        .map_err(|_| AnalysisError::Busy)?;
    Ok(match index {
        Some(name) => json!({"known": true, "name": name, "kind": "index"}),
        None => json!({"known": false}),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn warehouse(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-analysis-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        let c = Connection::open(&path).unwrap();
        c.execute_batch(
            "CREATE TABLE days (day DATE, status VARCHAR);
             INSERT INTO days VALUES (DATE '2026-10-15', 'loaded'), (DATE '2026-10-16', 'loaded'), (DATE '2026-10-17', 'no_session');
             CREATE TABLE universe (symbol VARCHAR, kind VARCHAR, name VARCHAR, isin VARCHAR, updated_at TIMESTAMP);
             INSERT INTO universe VALUES ('SBIN', 'stock', 'State Bank of India', 'X', now());
             CREATE TABLE screen (symbol VARCHAR, kind VARCHAR, name VARCHAR, sector VARCHAR, data_as_of DATE, close DOUBLE, ret_1d DOUBLE, ret_1m DOUBLE,
               ret_3m DOUBLE, ret_6m DOUBLE, ret_1y DOUBLE, from_52w_high DOUBLE, from_52w_low DOUBLE, traded_value_cr DOUBLE, rsi_14 DOUBLE, trend VARCHAR,
               sma50_vs_200 DOUBLE, market_cap_cr DOUBLE, pe DOUBLE, pb DOUBLE, roe DOUBLE, net_margin DOUBLE, debt_to_equity DOUBLE, dividend_yield DOUBLE,
               earnings_yield DOUBLE, revenue_growth DOUBLE, flags INTEGER, statements_age_days INTEGER, built_at TIMESTAMP);
             INSERT INTO screen VALUES ('SBIN', 'stock', 'State Bank of India', 'Banks', DATE '2026-10-16', 962.6, 0.27, 3.1, 5.0, 8.0, 22.0, -2.0, 40.0,
               1500.0, 55.5, 'up', 4.2, 850000.0, 9.8, 1.4, 0.17, 0.2, NULL, 0.018, 0.1, 0.12, 0, 20, now());
             CREATE TABLE packets (symbol VARCHAR, kind VARCHAR, data_as_of DATE, built_at TIMESTAMP, flags VARCHAR, payload VARCHAR);
             INSERT INTO packets VALUES ('SBIN', 'technical', DATE '2026-10-16', now(), '[]',
               '{\"metrics\": {\"rsi_14\": {\"value\": 55.5, \"unit\": \"index\", \"window\": \"14 days\"}, \"trend_daily\": {\"value\": \"up\", \"unit\": \"label\"}}, \"missing\": []}'),
               ('SBIN', 'fundamentals', DATE '2026-10-16', now(), '[]',
               '{\"metrics\": {\"pe\": {\"value\": 9.8, \"unit\": \"x\", \"group\": \"valuation\"}}, \"data_quality_flags\": [\"statements are old\"], \"missing\": [\"fcf_yield\"], \"sector\": \"Banks\"}');
             INSERT INTO packets VALUES ('BRK', 'technical', DATE '2026-10-16', now(), '[\"price break with no recorded action: 2026-02-27 (x0.1)\"]',
               '{\"metrics\": {}, \"data_quality_flags\": [\"price break with no recorded action: 2026-02-27 (x0.1)\", \"thin trading\"], \"missing\": []}');
             CREATE TABLE index_bars (index_name VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE);
             INSERT INTO index_bars VALUES ('Nifty 50', DATE '2026-10-16', 1, 2, 0.5, 1.5);
             CREATE TABLE verdicts (symbol VARCHAR, made_at TIMESTAMP, data_as_of DATE, close DOUBLE, annual_period DATE, quarter DATE, payload VARCHAR);
             INSERT INTO verdicts VALUES ('SBIN', TIMESTAMP '2026-10-10 10:00:00', DATE '2026-10-09', 950.0, NULL, NULL, '{\"verdict\": {\"verdict\": \"Hold\", \"conviction\": 10}}'),
               ('SBIN', TIMESTAMP '2026-10-12 10:00:00', DATE '2026-10-12', 955.0, NULL, NULL, '{\"verdict\": {\"verdict\": \"Underweight\", \"conviction\": 5}}');",
        )
        .unwrap();
        path
    }

    #[test]
    fn a_known_symbol_returns_everything_stored() {
        let a = analysis(&warehouse("full"), "SBIN").unwrap();
        assert_eq!(a["name"], "State Bank of India");
        assert_eq!(a["market_day"], "2026-10-16"); // the latest loaded day, not the no_session one
        assert_eq!(a["screen"]["pe"], 9.8);
        assert_eq!(a["screen"]["trend"], "up");
        assert!(a["screen"]["debt_to_equity"].is_null());
        assert_eq!(a["screen"]["sector"], "Banks");
        let tech = &a["technical"]["metrics"];
        assert_eq!(tech.as_array().unwrap().len(), 2);
        assert_eq!(tech[0]["name"], "rsi_14");
        assert_eq!(a["fundamentals"]["flags"][0], "statements are old");
        assert_eq!(a["fundamentals"]["missing"][0], "fcf_yield");
        assert_eq!(a["verdict"]["payload"]["verdict"]["verdict"], "Underweight"); // the newest of two
    }

    #[test]
    fn warnings_stored_in_the_flags_column_are_shown_once_beside_the_payload_ones() {
        let a = analysis(&warehouse("flags"), "BRK").unwrap();
        let flags: Vec<&str> = a["technical"]["flags"].as_array().unwrap().iter().map(|f| f.as_str().unwrap()).collect();
        assert_eq!(flags, vec!["price break with no recorded action: 2026-02-27 (x0.1)", "thin trading"]);
    }

    #[test]
    fn the_whole_screen_comes_back_as_rows_with_blanks_as_null() {
        let a = screen_all(&warehouse("screen")).unwrap();
        assert_eq!(a["columns"][0], "symbol");
        assert_eq!(a["market_day"], "2026-10-16");
        let cols: Vec<&str> = a["columns"].as_array().unwrap().iter().map(|c| c.as_str().unwrap()).collect();
        let row = &a["rows"][0];
        let at = |name: &str| row[cols.iter().position(|c| *c == name).unwrap()].clone();
        assert_eq!(at("symbol"), "SBIN");
        assert_eq!(at("pe"), 9.8);
        assert_eq!(at("trend"), "up");
        assert!(at("debt_to_equity").is_null()); // a blank is not a zero
    }

    #[test]
    fn each_row_carries_its_industry_and_the_indices_it_is_in_and_the_columns_are_null_without_the_table() {
        let none = screen_all(&warehouse("screen_no_members")).unwrap();
        let cols = |a: &Value| -> Vec<String> { a["columns"].as_array().unwrap().iter().map(|c| c.as_str().unwrap().to_string()).collect() };
        let at = |a: &Value, name: &str| a["rows"][0][cols(a).iter().position(|c| c == name).unwrap()].clone();
        assert!(at(&none, "industry").is_null() && at(&none, "indices").is_null());

        let path = warehouse("screen_members");
        Connection::open(&path)
            .unwrap()
            .execute_batch(
                "CREATE TABLE index_members (index_name VARCHAR, symbol VARCHAR, industry VARCHAR, fetched_at TIMESTAMP);
                 INSERT INTO index_members VALUES ('NIFTY 500', 'SBIN', 'Financial Services', now()), ('NIFTY 500', 'TCS', 'Information Technology', now()),
                   ('NIFTY BANK', 'SBIN', 'Banking', now()), ('NIFTY 50', 'SBIN', 'Financial Services', now());",
            )
            .unwrap();
        let a = screen_all(&path).unwrap();
        assert_eq!(at(&a, "industry"), "Financial Services"); // the largest index holding it decides (NIFTY 500 has two rows)
        assert_eq!(at(&a, "indices"), "NIFTY 50|NIFTY 500|NIFTY BANK");
    }

    #[test]
    fn the_value_and_industry_columns_come_through_when_the_table_has_them_and_are_null_when_it_does_not() {
        let cols = |a: &Value| -> Vec<String> { a["columns"].as_array().unwrap().iter().map(|c| c.as_str().unwrap().to_string()).collect() };
        let at = |a: &Value, name: &str| a["rows"][0][cols(a).iter().position(|c| c == name).unwrap()].clone();
        let old = screen_all(&warehouse("screen_old_columns")).unwrap(); // the fixture table has none of the later columns
        for name in ["industry_group", "pe_vs_industry", "value_base", "price_vs_value", "value_position", "implied_return", "valued_on"] {
            assert!(at(&old, name).is_null(), "{name} should be null for a table built before it existed");
        }
        assert_eq!(at(&old, "pe"), 9.8); // the columns it does have are untouched

        let path = warehouse("screen_new_columns");
        Connection::open(&path)
            .unwrap()
            .execute_batch(
                "ALTER TABLE screen ADD COLUMN industry_group VARCHAR; ALTER TABLE screen ADD COLUMN pe_vs_industry DOUBLE; ALTER TABLE screen ADD COLUMN pb_vs_industry DOUBLE;
                 ALTER TABLE screen ADD COLUMN roe_vs_industry DOUBLE; ALTER TABLE screen ADD COLUMN value_base DOUBLE; ALTER TABLE screen ADD COLUMN price_vs_value DOUBLE;
                 ALTER TABLE screen ADD COLUMN value_position INTEGER; ALTER TABLE screen ADD COLUMN implied_return DOUBLE; ALTER TABLE screen ADD COLUMN valued_on DATE;
                 UPDATE screen SET industry_group = 'Banks (Regional)', pe_vs_industry = 0.8, value_base = 1100.0, price_vs_value = -0.125, value_position = 0, implied_return = 0.13,
                   valued_on = DATE '2026-10-01';",
            )
            .unwrap();
        let a = screen_all(&path).unwrap();
        assert_eq!(at(&a, "industry_group"), "Banks (Regional)");
        assert_eq!(at(&a, "pe_vs_industry"), 0.8);
        assert_eq!(at(&a, "price_vs_value"), -0.125);
        assert_eq!(at(&a, "value_position"), 0.0);
        assert_eq!(at(&a, "valued_on"), "2026-10-01"); // a date stays text
        assert!(at(&a, "pb_vs_industry").is_null()); // not every stock has every figure
    }

    #[test]
    fn a_listed_stock_and_an_index_are_known_and_a_typo_is_not() {
        let w = warehouse("known");
        assert_eq!(known(&w, "SBIN").unwrap()["known"], true);
        assert_eq!(known(&w, "SBIN").unwrap()["name"], "State Bank of India");
        assert_eq!(known(&w, "SBIN").unwrap()["isin"], "X");
        assert!(known(&w, "NIFTY 50").unwrap().get("isin").is_none()); // an index has none
        assert_eq!(known(&w, "NIFTY 50").unwrap()["kind"], "index");
        assert_eq!(known(&w, "ZZZFAKE").unwrap()["known"], false);
    }

    #[test]
    fn an_unknown_symbol_answers_with_nulls() {
        let a = analysis(&warehouse("none"), "ZZZZ").unwrap();
        assert!(a["screen"].is_null() && a["technical"].is_null() && a["fundamentals"].is_null() && a["verdict"].is_null());
        assert_eq!(a["market_day"], "2026-10-16");
    }

    #[test]
    fn a_missing_file_is_busy() {
        assert_eq!(analysis(Path::new("no/such.duckdb"), "SBIN"), Err(AnalysisError::Busy));
    }
}
