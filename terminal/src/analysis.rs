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
    let (as_of, payload): (String, String) = conn
        .query_row("SELECT CAST(data_as_of AS VARCHAR), payload FROM packets WHERE symbol = ? AND kind = ?", [symbol, kind], |r| Ok((r.get(0)?, r.get(1)?)))
        .optional()
        .ok()
        .flatten()?;
    let body: Value = serde_json::from_str(&payload).ok()?;
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
        "flags": body.get("data_quality_flags").cloned().unwrap_or_else(|| json!([])),
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
