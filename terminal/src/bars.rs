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

/// A stock's daily bars with recorded splits and bonuses applied, what the chart needs so a split is not drawn as a cliff.
#[derive(Debug, Clone, Deserialize)]
pub struct Adjusted {
    pub bars: Vec<Bar>,
    /// Large overnight price breaks no record explains (inside the last year).
    #[serde(default)]
    pub flags: Vec<String>,
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

/// Companies whose exchange symbol changed while the company, and so its price history, carried on (new symbol, old symbol). The warehouse
/// keeps each symbol's bars under the name it traded as, so a chart of the new symbol would start on the rename day; the old bars are joined
/// behind it. A demerger (Tata Motors into TMPV) is not here: its price steps down at the split and needs its own treatment.
pub const PREDECESSORS: &[(&str, &str)] = &[("SHRIRAMFIN", "SRTRANSFIN"), ("ETERNAL", "ZOMATO"), ("TATACONSUM", "TATAGLOBAL")];

/// A price step between the old symbol's last bar and the new one's first bar beyond this is worth telling the reader about.
const JOIN_STEP_WARN: f64 = 0.25;

pub fn predecessor(symbol: &str) -> Option<&'static str> {
    PREDECESSORS.iter().find(|(new, _)| new.eq_ignore_ascii_case(symbol)).map(|(_, old)| *old)
}

/// What the adjustment did to the newer series on its first day: the adjusted close over the printed one. 1.0 when either is missing,
/// they are on different days, or a close is not positive (then the older bars are joined as printed).
pub fn join_scale(adjusted_first: Option<&Bar>, raw_first: Option<&Bar>) -> f64 {
    match (adjusted_first, raw_first) {
        (Some(a), Some(r)) if a.t == r.t && a.c > 0.0 && r.c > 0.0 => a.c / r.c,
        _ => 1.0,
    }
}

/// `older` in front of `newer`. The older prices are multiplied by `scale` and its volume divided by it, so a split or bonus that the newer
/// symbol's adjustment has already taken out runs through the older bars as well. Older bars on or after the newer's first day are dropped.
pub fn join_history(older: &[Bar], newer: &[Bar], scale: f64) -> Vec<Bar> {
    let Some(first) = newer.first() else { return Vec::new() };
    let mut out: Vec<Bar> = older
        .iter()
        .filter(|b| b.t < first.t)
        .map(|b| Bar { t: b.t, o: b.o * scale, h: b.h * scale, l: b.l * scale, c: b.c * scale, v: if scale > 0.0 { b.v / scale } else { b.v } })
        .collect();
    out.extend_from_slice(newer);
    out
}

impl Bars {
    /// `bars` (a stock's whole daily history, adjusted when `adjusted` is true) with its old symbol's history in front when it has one.
    /// Returns the old symbol that was joined and, when the join is not smooth, a sentence saying so.
    pub async fn with_predecessor(&self, symbol: &str, bars: Vec<Bar>, adjusted: bool) -> (Vec<Bar>, Option<(&'static str, String)>) {
        let Some(old) = predecessor(symbol) else { return (bars, None) };
        if bars.is_empty() {
            return (bars, None);
        }
        let older = match if adjusted { self.adjusted.get(old).await } else { None } {
            Some(a) => a.bars.clone(),
            None => {
                let (path, name) = (self.warehouse.clone(), old.to_string());
                match tokio::task::spawn_blocking(move || daily_bars(&path, &name, 5000)).await {
                    Ok(Ok(raw)) => raw,
                    _ => return (bars, None),
                }
            }
        };
        let scale = if adjusted {
            let (path, name) = (self.warehouse.clone(), symbol.to_ascii_uppercase());
            let raw = tokio::task::spawn_blocking(move || daily_bars(&path, &name, 5000)).await.ok().and_then(Result::ok).unwrap_or_default();
            join_scale(bars.first(), raw.first())
        } else {
            1.0
        };
        let joined = join_history(&older, &bars, scale);
        let added = joined.len() - bars.len();
        if added == 0 {
            return (bars, None);
        }
        let (last_old, first_new) = (&joined[added - 1], &joined[added]);
        let step = if last_old.c > 0.0 { first_new.c / last_old.c - 1.0 } else { 0.0 };
        let note = if step.abs() > JOIN_STEP_WARN {
            format!("{old} joins here with a {:+.0}% step that no record explains.", step * 100.0)
        } else {
            String::new()
        };
        (joined, Some((old, note)))
    }
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
        assert_eq!(yahoo_params("5m"), Some(("5m", "1mo")));
        assert_eq!(yahoo_params("1h"), Some(("60m", "1y")));
        assert_eq!(yahoo_params("30m"), Some(("30m", "1mo")));
        assert_eq!(yahoo_params("2m"), None); // built from 1-minute bars by the page
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

#[cfg(test)]
mod adjusted_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn adjusted() -> Adjusted {
        Adjusted { bars: vec![Bar { t: 1, o: 1.0, h: 1.0, l: 1.0, c: 1.0, v: 1.0 }], flags: vec![] }
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

    fn bar(t: i64, c: f64, v: f64) -> Bar {
        Bar { t, o: c, h: c, l: c, c, v }
    }

    #[test]
    fn older_bars_are_scaled_and_dropped_from_the_newer_first_day() {
        let older = [bar(1, 100.0, 10.0), bar(2, 110.0, 20.0), bar(3, 999.0, 1.0)];
        let newer = [bar(3, 24.0, 100.0), bar(4, 25.0, 100.0)];
        let joined = join_history(&older, &newer, 0.2);
        assert_eq!(joined.iter().map(|b| b.t).collect::<Vec<_>>(), vec![1, 2, 3, 4]);
        assert_eq!(joined[0].c, 20.0);
        assert_eq!(joined[0].v, 50.0); // fewer, dearer shares became more, cheaper ones
        assert_eq!(joined[2].c, 24.0); // the newer bars are untouched
        assert!(join_history(&older, &[], 0.2).is_empty());
    }

    #[test]
    fn the_scale_is_the_adjustment_on_the_newer_first_day_or_one() {
        let (a, r) = (bar(5, 277.85, 1.0), bar(5, 1389.25, 1.0));
        assert!((join_scale(Some(&a), Some(&r)) - 0.2).abs() < 1e-9);
        assert_eq!(join_scale(Some(&a), Some(&bar(6, 1389.25, 1.0))), 1.0); // not the same day
        assert_eq!(join_scale(Some(&a), None), 1.0);
        assert_eq!(join_scale(None, None), 1.0);
    }

    #[test]
    fn only_the_listed_renames_have_a_predecessor() {
        assert_eq!(predecessor("shriramfin"), Some("SRTRANSFIN"));
        assert_eq!(predecessor("ETERNAL"), Some("ZOMATO"));
        assert_eq!(predecessor("TATACONSUM"), Some("TATAGLOBAL"));
        assert_eq!(predecessor("SBIN"), None);
        assert_eq!(predecessor("TMPV"), None); // a demerger, not a rename
    }

    fn warehouse(name: &str, rows: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-join-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        let c = Connection::open(&path).unwrap();
        c.execute_batch(&format!(
            "CREATE TABLE bars (symbol VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, series VARCHAR, source VARCHAR, loaded_at TIMESTAMP);
             INSERT INTO bars VALUES {rows};"
        ))
        .unwrap();
        path
    }

    fn row(symbol: &str, day: &str, close: f64, volume: f64) -> String {
        format!("('{symbol}', DATE '{day}', {close}, {close}, {close}, {close}, {volume}, 'EQ', 't', now())")
    }

    /// An adjuster that takes `factor` out of one symbol's bars (as a later split would) and leaves the others as printed.
    fn adjuster(path: PathBuf, symbol: &'static str, factor: f64) -> AdjustedDaily {
        AdjustedDaily::new(Arc::new(move |s: String| {
            let path = path.clone();
            Box::pin(async move {
                let mut bars = daily_bars(&path, &s, 5000).ok()?;
                if s == symbol {
                    for b in &mut bars {
                        (b.o, b.h, b.l, b.c, b.v) = (b.o * factor, b.h * factor, b.l * factor, b.c * factor, b.v / factor);
                    }
                }
                Some(Adjusted { bars, flags: vec![] })
            })
        }))
    }

    fn bars_over(path: &Path, adjusted: AdjustedDaily) -> Bars {
        Bars { warehouse: path.to_path_buf(), intraday: Intraday::new(Arc::new(|_, _| Box::pin(async { None }))), adjusted }
    }

    #[tokio::test]
    async fn a_renamed_stock_gets_its_old_symbols_history_scaled_to_match_the_split_that_followed() {
        let rows = [
            row("SRTRANSFIN", "2022-12-15", 1393.2, 100.0),
            row("SRTRANSFIN", "2022-12-19", 1372.25, 100.0),
            row("SHRIRAMFIN", "2022-12-20", 1389.25, 50.0),
            row("SHRIRAMFIN", "2022-12-21", 1370.0, 60.0),
        ]
        .join(",");
        let path = warehouse("a", &rows);
        let bars = bars_over(&path, adjuster(path.clone(), "SHRIRAMFIN", 0.2));
        let own = bars.adjusted.get("SHRIRAMFIN").await.unwrap().bars.clone();
        let (joined, said) = bars.with_predecessor("SHRIRAMFIN", own, true).await;
        assert_eq!(joined.len(), 4);
        let closes: Vec<f64> = joined.iter().map(|b| (b.c * 100.0).round() / 100.0).collect();
        assert_eq!(closes, vec![278.64, 274.45, 277.85, 274.0]); // the old bars now sit beside the new ones, no 5x cliff
        assert_eq!(joined[0].v, 500.0);
        let (old, note) = said.unwrap();
        assert_eq!(old, "SRTRANSFIN");
        assert_eq!(note, ""); // a 1.2% step is not worth a warning

        let raw_own = daily_bars(&path, "SHRIRAMFIN", 5000).unwrap();
        let (raw, _) = bars.with_predecessor("SHRIRAMFIN", raw_own, false).await;
        assert_eq!(raw.iter().map(|b| b.c).collect::<Vec<_>>(), vec![1393.2, 1372.25, 1389.25, 1370.0]); // as printed, they already join
    }

    #[tokio::test]
    async fn a_join_that_steps_by_a_quarter_or_more_says_so_and_other_stocks_are_left_alone() {
        let rows = [row("ZOMATO", "2025-04-08", 100.0, 1.0), row("ETERNAL", "2025-04-09", 150.0, 1.0)].join(",");
        let path = warehouse("b", &rows);
        let bars = bars_over(&path, adjuster(path.clone(), "NONE", 1.0));
        let own = daily_bars(&path, "ETERNAL", 5000).unwrap();
        let (joined, said) = bars.with_predecessor("ETERNAL", own.clone(), false).await;
        assert_eq!(joined.len(), 2);
        assert!(said.unwrap().1.contains("+50% step"));
        let (same, none) = bars.with_predecessor("SBIN", own.clone(), false).await;
        assert_eq!((same, none), (own, None));
    }
}
