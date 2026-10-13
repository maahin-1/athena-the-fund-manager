//! Bars for the chart: daily from the warehouse (read-only) and 1, 5 and 15 minute from Yahoo, fetched in the background and cached.
use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use duckdb::{AccessMode, Config, Connection};
use serde::Serialize;
use serde_json::Value;

pub const CACHE_FOR: Duration = Duration::from_secs(60);
pub const DEFAULT_LIMIT: usize = 500;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Bar {
    /// Unix seconds. For a daily bar, midnight UTC of the trading date.
    pub t: i64,
    pub o: f64,
    pub h: f64,
    pub l: f64,
    pub c: f64,
    pub v: f64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum BarsError {
    Busy,
}

/// The warehouse's table for an index (`Nifty 50`) or a stock (`SBIN`); the page asks with upper-case names.
fn is_index_name(symbol: &str) -> bool {
    symbol.to_ascii_uppercase().starts_with("NIFTY ")
}

/// Newest `limit` daily bars, oldest first. A stock reads `bars`; an index name reads `index_bars` (matched ignoring case).
pub fn daily_bars(path: &Path, symbol: &str, limit: usize) -> Result<Vec<Bar>, BarsError> {
    let config = Config::default().access_mode(AccessMode::ReadOnly).map_err(|_| BarsError::Busy)?;
    let conn = Connection::open_with_flags(path, config).map_err(|_| BarsError::Busy)?;
    let sql = if is_index_name(symbol) {
        "SELECT CAST(epoch(CAST(day AS TIMESTAMP)) AS BIGINT), open, high, low, close, 0.0 FROM index_bars WHERE lower(index_name) = lower(?) ORDER BY day DESC LIMIT ?"
    } else {
        "SELECT CAST(epoch(CAST(day AS TIMESTAMP)) AS BIGINT), open, high, low, close, COALESCE(volume, 0.0) FROM bars WHERE symbol = ? ORDER BY day DESC LIMIT ?"
    };
    let mut stmt = conn.prepare(sql).map_err(|_| BarsError::Busy)?;
    let rows = stmt
        .query_map(duckdb::params![symbol, limit as i64], |r| {
            Ok(Bar { t: r.get(0)?, o: r.get(1)?, h: r.get(2)?, l: r.get(3)?, c: r.get(4)?, v: r.get(5)? })
        })
        .map_err(|_| BarsError::Busy)?;
    let mut bars: Vec<Bar> = rows.filter_map(Result::ok).collect();
    bars.reverse();
    Ok(bars)
}

/// Yahoo's symbol for a name the page uses.
pub fn yahoo_symbol(symbol: &str) -> String {
    match symbol.to_ascii_uppercase().as_str() {
        "NIFTY 50" => "^NSEI".into(),
        "NIFTY BANK" => "^NSEBANK".into(),
        other => format!("{other}.NS"),
    }
}

/// `(interval, range)` Yahoo accepts for a page timeframe; `None` for a timeframe that is not intraday.
pub fn yahoo_params(tf: &str) -> Option<(&'static str, &'static str)> {
    match tf {
        "1m" => Some(("1m", "1d")),
        "5m" => Some(("5m", "5d")),
        "15m" => Some(("15m", "5d")),
        _ => None,
    }
}

/// Bars from a Yahoo chart reply; rows with a missing price are skipped.
pub fn parse_yahoo(body: &Value) -> Vec<Bar> {
    let result = &body["chart"]["result"][0];
    let (Some(times), Some(q)) = (result["timestamp"].as_array(), result["indicators"]["quote"].get(0)) else {
        return Vec::new();
    };
    let col = |name: &str, i: usize| q[name].get(i).and_then(Value::as_f64).map(|x| (x * 100.0).round() / 100.0); // Yahoo sends float noise such as 952.8499755859376
    times
        .iter()
        .enumerate()
        .filter_map(|(i, t)| {
            Some(Bar { t: t.as_i64()?, o: col("open", i)?, h: col("high", i)?, l: col("low", i)?, c: col("close", i)?, v: col("volume", i).unwrap_or(0.0) })
        })
        .collect()
}

pub type Fetcher = Arc<dyn Fn(String, String) -> Pin<Box<dyn Future<Output = Option<Vec<Bar>>> + Send>> + Send + Sync>;

pub fn yahoo_fetcher(client: reqwest::Client) -> Fetcher {
    Arc::new(move |symbol: String, tf: String| {
        let client = client.clone();
        Box::pin(async move {
            let (interval, range) = yahoo_params(&tf)?;
            let url = format!("https://query1.finance.yahoo.com/v8/finance/chart/{}", yahoo_symbol(&symbol));
            let body: Value = client
                .get(url)
                .query(&[("interval", interval), ("range", range)])
                .header("User-Agent", "Mozilla/5.0")
                .timeout(Duration::from_secs(20))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok()?;
            let bars = parse_yahoo(&body);
            (!bars.is_empty()).then_some(bars)
        })
    })
}

#[derive(Default)]
struct YahooInner {
    cache: HashMap<(String, String), (Instant, Vec<Bar>)>,
    loading: HashSet<(String, String)>,
}

/// Intraday bars: returns what is cached at once and starts a background fetch when the cache is missing or older than `CACHE_FOR`.
#[derive(Clone)]
pub struct Intraday {
    inner: Arc<Mutex<YahooInner>>,
    fetch: Fetcher,
}

impl Intraday {
    pub fn new(fetch: Fetcher) -> Self {
        Intraday { inner: Arc::new(Mutex::new(YahooInner::default())), fetch }
    }

    /// `(bars, loading)`: the cached bars (possibly old, possibly none) and whether a fetch is running.
    pub fn get(&self, symbol: &str, tf: &str) -> (Vec<Bar>, bool) {
        let key = (symbol.to_ascii_uppercase(), tf.to_string());
        let mut g = self.inner.lock().unwrap();
        let cached = g.cache.get(&key).cloned();
        let fresh = cached.as_ref().is_some_and(|(at, _)| at.elapsed() < CACHE_FOR);
        if !fresh && !g.loading.contains(&key) {
            g.loading.insert(key.clone());
            let (inner, fetch, k) = (self.inner.clone(), self.fetch.clone(), key.clone());
            tokio::spawn(async move {
                let result = fetch(k.0.clone(), k.1.clone()).await;
                let mut g = inner.lock().unwrap();
                g.loading.remove(&k);
                if let Some(bars) = result {
                    g.cache.insert(k, (Instant::now(), bars));
                }
            });
        }
        let loading = g.loading.contains(&key);
        (cached.map(|(_, b)| b).unwrap_or_default(), loading)
    }
}

pub struct Bars {
    pub warehouse: PathBuf,
    pub intraday: Intraday,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn temp_warehouse(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-bars-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        let c = Connection::open(&path).unwrap();
        c.execute_batch(
            "CREATE TABLE bars (symbol VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, series VARCHAR, source VARCHAR, loaded_at TIMESTAMP);
             INSERT INTO bars VALUES ('SBIN', DATE '2026-10-09', 1, 2, 0.5, 1.5, 100, 'EQ', 't', now()),
                                    ('SBIN', DATE '2026-10-12', 2, 3, 1.5, 2.5, 200, 'EQ', 't', now()),
                                    ('SBIN', DATE '2026-10-13', 3, 4, 2.5, 3.5, 300, 'EQ', 't', now()),
                                    ('TCS',  DATE '2026-10-13', 9, 9, 9, 9, 9, 'EQ', 't', now());
             CREATE TABLE index_bars (index_name VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, pe DOUBLE, pb DOUBLE, div_yield DOUBLE);
             INSERT INTO index_bars VALUES ('Nifty 50', DATE '2026-10-13', 24000, 24900, 23900, 24800, 20, 3, 1.2);",
        )
        .unwrap();
        path
    }

    #[test]
    fn daily_bars_come_back_oldest_first_and_honour_the_limit() {
        let path = temp_warehouse("a");
        let all = daily_bars(&path, "SBIN", 10).unwrap();
        assert_eq!(all.iter().map(|b| b.c).collect::<Vec<_>>(), vec![1.5, 2.5, 3.5]);
        assert_eq!(all[2].v, 300.0);
        assert_eq!(all[2].t, 1_791_849_600); // 2026-10-13 00:00 UTC
        let last_two = daily_bars(&path, "SBIN", 2).unwrap();
        assert_eq!(last_two.iter().map(|b| b.c).collect::<Vec<_>>(), vec![2.5, 3.5]);
    }

    #[test]
    fn an_index_name_reads_the_index_table_ignoring_case() {
        let path = temp_warehouse("b");
        let bars = daily_bars(&path, "NIFTY 50", 10).unwrap();
        assert_eq!(bars.len(), 1);
        assert_eq!(bars[0].c, 24800.0);
    }

    #[test]
    fn a_missing_file_is_busy_not_a_panic() {
        assert_eq!(daily_bars(Path::new("no/such/file.duckdb"), "SBIN", 5), Err(BarsError::Busy));
    }

    #[test]
    fn yahoo_reply_parses_and_skips_null_rows() {
        let body = json!({"chart": {"result": [{
            "timestamp": [1000, 1060, 1120],
            "indicators": {"quote": [{
                "open": [1.0, null, 3.0], "high": [2.0, null, 4.0], "low": [0.5, null, 2.5], "close": [1.5, null, 3.5], "volume": [10, null, 30]
            }]}
        }]}});
        let bars = parse_yahoo(&body);
        assert_eq!(bars.len(), 2);
        assert_eq!((bars[1].t, bars[1].c, bars[1].v), (1120, 3.5, 30.0));
        assert!(parse_yahoo(&json!({})).is_empty());
    }

    #[test]
    fn yahoo_names_and_intervals() {
        assert_eq!(yahoo_symbol("SBIN"), "SBIN.NS");
        assert_eq!(yahoo_symbol("NIFTY 50"), "^NSEI");
        assert_eq!(yahoo_symbol("nifty bank"), "^NSEBANK");
        assert_eq!(yahoo_params("5m"), Some(("5m", "5d")));
        assert_eq!(yahoo_params("1D"), None);
    }

    #[tokio::test]
    async fn the_cache_serves_a_second_call_without_a_second_fetch() {
        let calls = Arc::new(AtomicUsize::new(0));
        let counter = calls.clone();
        let fetch: Fetcher = Arc::new(move |_s, _t| {
            counter.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Some(vec![Bar { t: 1, o: 1.0, h: 1.0, l: 1.0, c: 1.0, v: 1.0 }]) })
        });
        let intraday = Intraday::new(fetch);
        let (first, loading) = intraday.get("SBIN", "1m");
        assert!(first.is_empty() && loading); // nothing yet; the fetch runs in the background
        for _ in 0..50 {
            tokio::time::sleep(Duration::from_millis(20)).await;
            if !intraday.get("SBIN", "1m").0.is_empty() {
                break;
            }
        }
        let (second, loading) = intraday.get("SBIN", "1m");
        assert_eq!(second.len(), 1);
        assert!(!loading);
        let _ = intraday.get("SBIN", "1m");
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}
