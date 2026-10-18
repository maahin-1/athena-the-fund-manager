//! Bars for the chart: daily from the warehouse (read-only) and 1, 5 and 15 minute from Yahoo, fetched in the background and cached.
use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use duckdb::{AccessMode, Config, Connection};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const CACHE_FOR: Duration = Duration::from_secs(60);
pub const DEFAULT_LIMIT: usize = 500;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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

/// Yahoo's symbol for a name the page uses. A stock is its symbol and `.NS`. Yahoo names an index by a code of its own that cannot be made
/// from the index's name, and only two are known here; any other index has no intraday source (`None`), so it is not asked for under a
/// stock's name (which only ever fails) and the page is told.
pub fn yahoo_symbol(symbol: &str) -> Option<String> {
    let upper = symbol.to_ascii_uppercase();
    match upper.as_str() {
        "NIFTY 50" => Some("^NSEI".into()),
        "NIFTY BANK" => Some("^NSEBANK".into()),
        _ if is_index_name(&upper) || upper == "INDIA VIX" => None,
        other => Some(format!("{other}.NS")),
    }
}

/// How long a failed intraday fetch is remembered, so a page that polls does not start the same failing request again and again.
pub const RETRY_AFTER: Duration = Duration::from_secs(300);

/// `(interval, range)` Yahoo accepts for a page timeframe; `None` for a timeframe that is not intraday.
pub fn yahoo_params(tf: &str) -> Option<(&'static str, &'static str)> {
    match tf {
        // the free source holds 5 days of 1-minute bars, 60 days of 5, 15 and 30-minute bars and two years of hourly ones; the page slices what it shows
        "1m" => Some(("1m", "5d")),
        "5m" => Some(("5m", "1mo")),
        "15m" => Some(("15m", "1mo")),
        "30m" => Some(("30m", "1mo")),
        "1h" => Some(("60m", "1y")),
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
            let url = format!("https://query1.finance.yahoo.com/v8/finance/chart/{}", yahoo_symbol(&symbol)?);
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
    failed: HashMap<(String, String), Instant>,
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
        let failed = g.failed.get(&key).is_some_and(|at| at.elapsed() < RETRY_AFTER);
        if !fresh && !failed && !g.loading.contains(&key) {
            g.loading.insert(key.clone());
            let (inner, fetch, k) = (self.inner.clone(), self.fetch.clone(), key.clone());
            tokio::spawn(async move {
                let result = fetch(k.0.clone(), k.1.clone()).await;
                let mut g = inner.lock().unwrap();
                g.loading.remove(&k);
                match result {
                    Some(bars) => {
                        g.failed.remove(&k);
                        g.cache.insert(k, (Instant::now(), bars));
                    }
                    None => {
                        g.failed.insert(k, Instant::now());
                    }
                }
            });
        }
        let loading = g.loading.contains(&key);
        (cached.map(|(_, b)| b).unwrap_or_default(), loading)
    }
}

/// A stock's daily bars with recorded splits and bonuses applied, what the chart needs so a split is not drawn as a cliff.
#[derive(Debug, Clone, Deserialize)]
pub struct Adjusted {
    pub bars: Vec<Bar>,
    /// Large overnight price breaks no record explains (inside the last year).
    #[serde(default)]
    pub flags: Vec<String>,
    /// The earlier symbols whose bars the history begins with (the helper joins them; see `athena.warehouse.renames`).
    #[serde(default)]
    pub joined: Vec<Joined>,
}

pub type AdjustFetcher = Arc<dyn Fn(String) -> Pin<Box<dyn Future<Output = Option<Adjusted>> + Send>> + Send + Sync>;

/// How long an adjusted history is kept. The warehouse changes once a night, and an adjustment needs a Python start (about a second).
pub const ADJUSTED_FOR: Duration = Duration::from_secs(30 * 60);
const ADJUST_LIMIT: Duration = Duration::from_secs(25);

/// Adjusted daily bars per stock, computed by the project's own Python (`athena.warehouse.chart_bars`, the same adjustment the
/// Streamlit charts use) and kept for `ADJUSTED_FOR`. A failure is not cached, so the next request tries again.
#[derive(Clone)]
pub struct AdjustedDaily {
    cache: Arc<Mutex<HashMap<String, (Instant, Arc<Adjusted>)>>>,
    fetch: AdjustFetcher,
}

impl AdjustedDaily {
    pub fn new(fetch: AdjustFetcher) -> Self {
        AdjustedDaily { cache: Arc::new(Mutex::new(HashMap::new())), fetch }
    }

    pub fn python(python: String, warehouse: PathBuf) -> Self {
        Self::new(Arc::new(move |symbol: String| {
            let (python, warehouse) = (python.clone(), warehouse.clone());
            Box::pin(async move {
                let mut cmd = tokio::process::Command::new(python);
                cmd.args(["-m", "athena.warehouse.chart_bars", &symbol, "--limit", "5000", "--warehouse"])
                    .arg(&warehouse)
                    .stdin(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .kill_on_drop(true);
                let out = tokio::time::timeout(ADJUST_LIMIT, cmd.output()).await.ok()?.ok()?;
                if !out.status.success() {
                    return None;
                }
                serde_json::from_slice::<Adjusted>(&out.stdout).ok().filter(|a| !a.bars.is_empty())
            })
        }))
    }

    pub async fn get(&self, symbol: &str) -> Option<Arc<Adjusted>> {
        let key = symbol.to_ascii_uppercase();
        if let Some((at, hit)) = self.cache.lock().unwrap().get(&key) {
            if at.elapsed() < ADJUSTED_FOR {
                return Some(hit.clone());
            }
        }
        let fresh = Arc::new((self.fetch)(key.clone()).await?);
        self.cache.lock().unwrap().insert(key, (Instant::now(), fresh.clone()));
        Some(fresh)
    }
}

pub struct Bars {
    pub warehouse: PathBuf,
    pub intraday: Intraday,
    pub adjusted: AdjustedDaily,
}

/// An earlier symbol of the same company whose prices run on into the symbol's own, as the warehouse's rename table records and has checked
/// against the bars (`athena.warehouse.renames`). Nothing here names a company.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Joined {
    pub symbol: String,
    /// The rename's date, `YYYY-MM-DD`.
    pub until: String,
    /// A sentence for the page.
    pub note: String,
}

fn joined_note(old: &str, until: &str, event: &str) -> String {
    let note = format!("Earlier history is {old}'s, the symbol until {until}.");
    if event.is_empty() {
        note
    } else {
        format!("{note} Around then: {event}.")
    }
}

/// How many renames back a chain is followed (the Python side stops at the same number).
const MAX_HOPS: usize = 5;

/// The renames that lead to `symbol` whose prices join, nearest first, each with the unix second of the old symbol's last bar. Empty
/// when the table is missing (a warehouse built before it existed) or holds nothing for the symbol.
fn rename_chain(conn: &Connection, symbol: &str) -> Vec<(Joined, i64)> {
    let mut out: Vec<(Joined, i64)> = Vec::new();
    let mut here = symbol.to_string();
    for _ in 0..MAX_HOPS {
        let Ok(mut stmt) = conn.prepare(
            "SELECT old, CAST(change_day AS VARCHAR), COALESCE(event, ''), CAST(epoch(CAST(last_old_day AS TIMESTAMP)) AS BIGINT) \
             FROM symbol_renames WHERE new = ? AND joinable ORDER BY change_day DESC",
        ) else {
            return out;
        };
        let Ok(rows) = stmt.query_map(duckdb::params![here], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, i64>(3)?))) else {
            return out;
        };
        let next = rows.filter_map(Result::ok).find(|(old, ..)| old != symbol && !out.iter().any(|(j, _)| &j.symbol == old));
        let Some((old, until, event, last)) = next else { break };
        out.push((Joined { note: joined_note(&old, &until, &event), symbol: old.clone(), until }, last));
        here = old;
    }
    out
}

/// Newest `limit` daily bars as the exchange printed them, oldest first, with the bars of the symbol's earlier symbols in front when the
/// symbol's own do not fill `limit` (a company that changed its symbol keeps one history). A stock reads `bars`; an index reads `index_bars`.
pub fn daily_bars_joined(path: &Path, symbol: &str, limit: usize) -> Result<(Vec<Bar>, Vec<Joined>), BarsError> {
    let mut bars = daily_bars(path, symbol, limit)?;
    if is_index_name(symbol) || bars.len() >= limit || bars.is_empty() {
        return Ok((bars, Vec::new()));
    }
    let config = Config::default().access_mode(AccessMode::ReadOnly).map_err(|_| BarsError::Busy)?;
    let chain = Connection::open_with_flags(path, config).map(|conn| rename_chain(&conn, symbol)).unwrap_or_default();
    let mut joined = Vec::new();
    for (step, last) in chain {
        let room = limit.saturating_sub(bars.len());
        if room == 0 {
            break;
        }
        let first = bars[0].t;
        let mut before: Vec<Bar> = daily_bars(path, &step.symbol, room)?.into_iter().filter(|b| b.t <= last && b.t < first).collect();
        if before.is_empty() {
            continue;
        }
        before.append(&mut bars);
        bars = before;
        joined.push(step);
    }
    joined.reverse();
    Ok((bars, joined))
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
        assert_eq!(yahoo_symbol("SBIN").as_deref(), Some("SBIN.NS"));
        assert_eq!(yahoo_symbol("NIFTY 50").as_deref(), Some("^NSEI"));
        assert_eq!(yahoo_symbol("nifty bank").as_deref(), Some("^NSEBANK"));
        assert_eq!(yahoo_symbol("NIFTY IT"), None); // an index with no known code is not sent to the stock endpoint
        assert_eq!(yahoo_symbol("India VIX"), None);
        assert_eq!(yahoo_params("5m"), Some(("5m", "1mo")));
        assert_eq!(yahoo_params("1h"), Some(("60m", "1y")));
        assert_eq!(yahoo_params("30m"), Some(("30m", "1mo")));
        assert_eq!(yahoo_params("2m"), None); // built from 1-minute bars by the page
        assert_eq!(yahoo_params("1D"), None);
    }

    #[tokio::test]
    async fn a_failed_fetch_is_remembered_so_polling_does_not_repeat_it_and_the_page_can_say_there_is_nothing() {
        let calls = Arc::new(AtomicUsize::new(0));
        let counter = calls.clone();
        let fetch: Fetcher = Arc::new(move |_s, _t| {
            counter.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { None })
        });
        let intraday = Intraday::new(fetch);
        let (_, loading) = intraday.get("NIFTY IT", "5m");
        assert!(loading);
        for _ in 0..50 {
            tokio::time::sleep(Duration::from_millis(20)).await;
            if !intraday.get("NIFTY IT", "5m").1 {
                break;
            }
        }
        let (bars, loading) = intraday.get("NIFTY IT", "5m");
        assert!(bars.is_empty() && !loading); // not loading any more: the page reads this as "no bars"
        for _ in 0..5 {
            let _ = intraday.get("NIFTY IT", "5m");
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
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

#[cfg(test)]
mod adjusted_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn adjusted() -> Adjusted {
        Adjusted { bars: vec![Bar { t: 1, o: 1.0, h: 1.0, l: 1.0, c: 1.0, v: 1.0 }], flags: vec![], joined: vec![] }
    }

    #[tokio::test]
    async fn an_adjusted_history_is_asked_for_once_and_then_kept() {
        let calls = Arc::new(AtomicUsize::new(0));
        let c = calls.clone();
        let daily = AdjustedDaily::new(Arc::new(move |_s| {
            c.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Some(adjusted()) })
        }));
        assert!(daily.get("sbin").await.is_some());
        assert!(daily.get("SBIN").await.is_some());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn a_failure_is_not_kept_so_the_next_request_tries_again() {
        let calls = Arc::new(AtomicUsize::new(0));
        let c = calls.clone();
        let daily = AdjustedDaily::new(Arc::new(move |_s| {
            let n = c.fetch_add(1, Ordering::SeqCst);
            Box::pin(async move { (n > 0).then(adjusted) })
        }));
        assert!(daily.get("SBIN").await.is_none());
        assert!(daily.get("SBIN").await.is_some());
        assert_eq!(calls.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn the_python_helper_output_is_read() {
        let body = br#"{"bars":[{"t":1792108800,"o":1.0,"h":2.0,"l":0.5,"c":1.5,"v":10.0}],"flags":["2026-01-14 (x0.2)"],"notes":["ignored"]}"#;
        let a: Adjusted = serde_json::from_slice(body).unwrap();
        assert_eq!((a.bars.len(), a.flags.len()), (1, 1));
    }
}

#[cfg(test)]
mod join_tests {
    use super::*;

    const TABLE: &str = "CREATE TABLE symbol_renames (old VARCHAR NOT NULL, new VARCHAR NOT NULL, change_day DATE NOT NULL, company VARCHAR, last_old_day DATE, first_new_day DATE, gap_days INTEGER, step DOUBLE, joinable BOOLEAN NOT NULL, retired BOOLEAN NOT NULL, event VARCHAR, reason VARCHAR, checked_at TIMESTAMP NOT NULL);";

    fn warehouse(name: &str, bars: &[(&str, &str, f64)], renames: &[(&str, &str, &str, &str, bool, &str)]) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-join-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        let c = Connection::open(&path).unwrap();
        c.execute_batch("CREATE TABLE bars (symbol VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, series VARCHAR, source VARCHAR, loaded_at TIMESTAMP);").unwrap();
        for (symbol, day, close) in bars {
            c.execute_batch(&format!("INSERT INTO bars VALUES ('{symbol}', DATE '{day}', {close}, {close}, {close}, {close}, 10, 'EQ', 't', now());")).unwrap();
        }
        c.execute_batch(TABLE).unwrap();
        c.execute_batch("CREATE TABLE index_bars (index_name VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, pe DOUBLE, pb DOUBLE, div_yield DOUBLE);").unwrap();
        for (old, new, change, last_old, joinable, event) in renames {
            c.execute_batch(&format!("INSERT INTO symbol_renames VALUES ('{old}', '{new}', DATE '{change}', 'Co', DATE '{last_old}', NULL, NULL, NULL, {joinable}, true, '{event}', '', now());")).unwrap();
        }
        path
    }

    fn closes(bars: &[Bar]) -> Vec<f64> {
        bars.iter().map(|b| b.c).collect()
    }

    #[test]
    fn a_renamed_symbol_gets_its_earlier_symbols_bars_in_front_as_printed() {
        let path = warehouse(
            "a",
            &[("A", "2022-12-15", 1.0), ("A", "2022-12-16", 2.0), ("B", "2022-12-19", 3.0), ("B", "2022-12-20", 4.0)],
            &[("A", "B", "2022-12-19", "2022-12-16", true, "")],
        );
        let (bars, joined) = daily_bars_joined(&path, "B", 100).unwrap();
        assert_eq!(closes(&bars), vec![1.0, 2.0, 3.0, 4.0]);
        assert_eq!(joined, vec![Joined { symbol: "A".into(), until: "2022-12-19".into(), note: "Earlier history is A's, the symbol until 2022-12-19.".into() }]);
    }

    #[test]
    fn a_chain_is_followed_oldest_first_and_an_event_is_in_the_note() {
        let path = warehouse(
            "b",
            &[("A", "2022-01-03", 1.0), ("B", "2022-06-01", 2.0), ("C", "2022-12-01", 3.0)],
            &[("A", "B", "2022-06-01", "2022-01-03", true, ""), ("B", "C", "2022-12-01", "2022-06-01", true, "Demerger (2022-11-20)")],
        );
        let (bars, joined) = daily_bars_joined(&path, "C", 100).unwrap();
        assert_eq!(closes(&bars), vec![1.0, 2.0, 3.0]);
        assert_eq!(joined.iter().map(|j| j.symbol.as_str()).collect::<Vec<_>>(), vec!["A", "B"]);
        assert!(joined[1].note.ends_with("Around then: Demerger (2022-11-20)."));
    }

    #[test]
    fn a_rename_whose_prices_do_not_join_a_missing_table_and_an_index_are_left_alone() {
        let path = warehouse("c", &[("A", "2018-01-02", 1.0), ("B", "2022-12-20", 4.0)], &[("A", "B", "2022-12-20", "2018-01-02", false, "")]);
        let (bars, joined) = daily_bars_joined(&path, "B", 100).unwrap();
        assert_eq!((closes(&bars), joined), (vec![4.0], vec![]));

        let none = temp_plain("d");
        assert_eq!(closes(&daily_bars_joined(&none, "B", 100).unwrap().0), vec![4.0]); // no table at all

        let (index, joined) = daily_bars_joined(&path, "NIFTY 50", 100).unwrap();
        assert!(index.is_empty() && joined.is_empty());
    }

    fn temp_plain(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-join-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        let c = Connection::open(&path).unwrap();
        c.execute_batch("CREATE TABLE bars (symbol VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, series VARCHAR, source VARCHAR, loaded_at TIMESTAMP); INSERT INTO bars VALUES ('B', DATE '2022-12-20', 4, 4, 4, 4, 1, 'EQ', 't', now());").unwrap();
        path
    }

    #[test]
    fn a_limit_the_own_bars_fill_does_not_read_the_earlier_symbol_and_a_rename_never_loops() {
        let path = warehouse(
            "e",
            &[("A", "2022-12-15", 1.0), ("B", "2022-12-19", 3.0), ("B", "2022-12-20", 4.0)],
            &[("A", "B", "2022-12-19", "2022-12-15", true, ""), ("B", "A", "2022-12-19", "2022-12-20", true, "")],
        );
        let (bars, joined) = daily_bars_joined(&path, "B", 2).unwrap();
        assert_eq!((closes(&bars), joined.len()), (vec![3.0, 4.0], 0));
        let (bars, joined) = daily_bars_joined(&path, "B", 5).unwrap();
        assert_eq!((closes(&bars), joined.len()), (vec![1.0, 3.0, 4.0], 1)); // B back to A, and A's own rename to B is not followed again
    }

    #[test]
    fn the_python_helpers_joined_list_is_read() {
        let body = br#"{"bars":[{"t":1,"o":1.0,"h":1.0,"l":1.0,"c":1.0,"v":1.0}],"flags":[],"notes":[],"joined":[{"symbol":"OLD","until":"2022-12-20","note":"Earlier history is OLD's, the symbol until 2022-12-20."}]}"#;
        let a: Adjusted = serde_json::from_slice(body).unwrap();
        assert_eq!(a.joined[0].symbol, "OLD");
        let old: Adjusted = serde_json::from_slice(br#"{"bars":[],"flags":[]}"#).unwrap();
        assert!(old.joined.is_empty()); // a helper that does not say is read as joining nothing
    }
}
