//! Bars for the chart: daily from the warehouse (read-only) and 1, 5 and 15 minute from Yahoo, fetched in the background and cached.
use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use duckdb::Connection;
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
pub fn is_index_name(symbol: &str) -> bool {
    symbol.to_ascii_uppercase().starts_with("NIFTY ")
}

/// Newest `limit` daily bars, oldest first. A stock reads `bars`; an index name reads `index_bars` (matched ignoring case).
pub fn daily_bars(path: &Path, symbol: &str, limit: usize) -> Result<Vec<Bar>, BarsError> {
    let conn = crate::snapshot::open(path, "bars").map_err(|_| BarsError::Busy)?.conn;
    bars_on(&conn, symbol, limit)
}

fn bars_on(conn: &Connection, symbol: &str, limit: usize) -> Result<Vec<Bar>, BarsError> {
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

    /// As `get`, but when nothing is cached and a fetch is running, waits up to `wait` for it.
    pub async fn get_waiting(&self, symbol: &str, tf: &str, wait: Duration) -> (Vec<Bar>, bool) {
        let deadline = Instant::now() + wait;
        loop {
            let (bars, loading) = self.get(symbol, tf);
            if !bars.is_empty() || !loading || Instant::now() >= deadline {
                return (bars, loading);
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
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
    /// Which data the helper read: the real file, or a validated snapshot generation and why (the helper opens the warehouse itself). Absent in a reply from an older helper.
    #[serde(default)]
    pub provenance: Option<Value>,
}

pub type AdjustFetcher = Arc<dyn Fn(String) -> Pin<Box<dyn Future<Output = Option<Adjusted>> + Send>> + Send + Sync>;

/// How long an adjusted history is kept. The warehouse changes once a night, and an adjustment needs a Python start (about a second).
pub const ADJUSTED_FOR: Duration = Duration::from_secs(30 * 60);
const ADJUST_LIMIT: Duration = Duration::from_secs(25);
/// How many adjusted-history fetches run at once (a helper or the command run directly), and how many may wait for a place.
const ADJUST_AT_ONCE: usize = 2;
const ADJUST_WAITING: usize = 8;

/// The adjusted-history command, run directly. It has an owner of its own: past the fetch's deadline it is ended and confirmed gone before its place is free. The deadline is the one the fetch began with, handed down
/// as it is (not as a duration worked out earlier, which a slow start would stretch).
pub(crate) async fn run_chart_bars(owner: &crate::bounded::Admission, permit: crate::bounded::Permit, cmd: tokio::process::Command, budget: &crate::bounded::Budget) -> Option<Adjusted> {
    let out = owner.run_command(permit, cmd, "chart_bars", budget).await.ok()?;
    if !out.success {
        return None;
    }
    serde_json::from_slice::<Adjusted>(&out.stdout).ok().filter(|a| !a.bars.is_empty())
}

/// One adjusted-history fetch: admission, the helper and the command run directly (`direct`, given the same budget) all draw on one deadline.
pub(crate) async fn fetch_adjusted<F, Fut>(helpers: Option<&Arc<crate::helper::Helpers>>, admission: &crate::bounded::Admission, args: Vec<String>, limit: Duration, direct: F) -> Option<Adjusted>
where
    F: FnOnce(crate::bounded::Permit, crate::bounded::Budget) -> Fut,
    Fut: Future<Output = Option<Adjusted>>,
{
    use crate::helper::Next;
    let budget = crate::bounded::Budget::new(limit);
    let permit = admission.admit("chart_bars", &budget).await.ok()?;
    if let Some(h) = helpers {
        match h.run_within(crate::helper::HelperOp::ChartBars, &args, &budget).await.next(crate::helper::HelperOp::ChartBars.idempotent()) {
            Next::Output(out) => {
                if let Some(a) = serde_json::from_slice::<Adjusted>(&out).ok().filter(|a| !a.bars.is_empty()) {
                    return Some(a);
                }
                // an answer that is not a history: the helper is finished with the work, so the command may be run once
            }
            Next::RunCommand => {}
            // refused, expired, cancelled or not confirmed gone: the work may still be going or was never accepted, and running it again beside it is not the answer
            Next::Stop(_) => return None,
        }
    }
    if budget.expired() {
        return None;
    }
    direct(permit, budget).await
}

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

    /// Asks a warm helper first (0.3 s), else starts the module for the request (one to two seconds). Returns the admission every path of a fetch goes through, so the status route can list it.
    pub fn python(python: String, warehouse: PathBuf, helpers: Option<Arc<crate::helper::Helpers>>, journal: Option<PathBuf>) -> (Self, Arc<crate::bounded::Admission>) {
        Self::python_with(python, warehouse, helpers, ADJUST_LIMIT, journal)
    }

    /// As `python`, with the deadline of one request, and the admission every path of it goes through (returned so the status route can list it).
    pub fn python_with(python: String, warehouse: PathBuf, helpers: Option<Arc<crate::helper::Helpers>>, limit: Duration, journal: Option<PathBuf>) -> (Self, Arc<crate::bounded::Admission>) {
        let admission = Arc::new(match journal {
            Some(path) => crate::bounded::Admission::with_journal("adjusted_bars", ADJUST_AT_ONCE, ADJUST_WAITING, path),
            None => crate::bounded::Admission::new("adjusted_bars", ADJUST_AT_ONCE, ADJUST_WAITING),
        });
        let adm = admission.clone();
        let daily = Self::new(Arc::new(move |symbol: String| {
            let (python, warehouse, helpers, adm) = (python.clone(), warehouse.clone(), helpers.clone(), adm.clone());
            Box::pin(async move {
                let args = vec![symbol.clone(), "--limit".to_string(), "5000".to_string(), "--warehouse".to_string(), warehouse.to_string_lossy().into_owned()];
                let owner = adm.clone();
                fetch_adjusted(helpers.as_ref(), &adm, args, limit, move |permit, budget| async move {
                    let mut cmd = tokio::process::Command::new(python);
                    cmd.args(["-m", "athena.warehouse.chart_bars", &symbol, "--limit", "5000", "--warehouse"]).arg(&warehouse);
                    run_chart_bars(&owner, permit, cmd, &budget).await
                })
                .await
            })
        }));
        (daily, admission)
    }
    pub async fn get(&self, symbol: &str) -> Option<Arc<Adjusted>> {
        self.get_served(symbol).await.map(|(a, _)| a)
    }

    /// As `get`, with whether the answer came from the cache and how old it is: the data is described by the provenance it was *computed* with, and a caller that serves it says that it was cached.
    pub async fn get_served(&self, symbol: &str) -> Option<(Arc<Adjusted>, CacheState)> {
        let key = symbol.to_ascii_uppercase();
        if let Some((at, hit)) = self.cache.lock().unwrap().get(&key) {
            if at.elapsed() < ADJUSTED_FOR {
                return Some((hit.clone(), CacheState { hit: true, age_secs: at.elapsed().as_secs() }));
            }
        }
        let fresh = Arc::new((self.fetch)(key.clone()).await?);
        self.cache.lock().unwrap().insert(key, (Instant::now(), fresh.clone()));
        Some((fresh, CacheState { hit: false, age_secs: 0 }))
    }
}

/// Whether an adjusted history was served from the cache, and how many seconds ago it was computed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CacheState {
    pub hit: bool,
    pub age_secs: u64,
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
    daily_bars_with_provenance(path, symbol, limit).map(|(bars, joined, _)| (bars, joined)).map_err(|e| e.error)
}

/// The warehouse could not be opened or read for a daily-bars request. `failure` is the provenance of the failure (why the real file failed and why no snapshot generation replaced it).
pub struct BarsFailure {
    pub error: BarsError,
    pub failure: Option<Value>,
}

/// As `daily_bars_joined`, from **one** open of the warehouse (so every bar and every rename of a response come from the same data), with the provenance of that open: the real file, or a validated
/// snapshot generation and why.
pub fn daily_bars_with_provenance(path: &Path, symbol: &str, limit: usize) -> Result<(Vec<Bar>, Vec<Joined>, crate::snapshot::Provenance), BarsFailure> {
    // not registered here: the server registers the provenance of the response it serves (with the date of the last bar), at the point it serves it
    let opened = crate::snapshot::open_unrecorded(path).map_err(|f| BarsFailure { error: BarsError::Busy, failure: Some(f.to_value()) })?;
    let busy = |_| BarsFailure { error: BarsError::Busy, failure: None };
    let conn = &opened.conn;
    let mut bars = bars_on(conn, symbol, limit).map_err(busy)?;
    if is_index_name(symbol) || bars.len() >= limit || bars.is_empty() {
        return Ok((bars, Vec::new(), opened.provenance));
    }
    let chain = rename_chain(conn, symbol);
    let mut joined = Vec::new();
    for (step, last) in chain {
        let room = limit.saturating_sub(bars.len());
        if room == 0 {
            break;
        }
        let first = bars[0].t;
        let mut before: Vec<Bar> = bars_on(conn, &step.symbol, room).map_err(busy)?.into_iter().filter(|b| b.t <= last && b.t < first).collect();
        if before.is_empty() {
            continue;
        }
        before.append(&mut bars);
        bars = before;
        joined.push(step);
    }
    joined.reverse();
    Ok((bars, joined, opened.provenance))
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

    #[tokio::test]
    async fn a_first_request_can_wait_for_the_fetch_and_a_slow_one_is_not_waited_for_past_the_limit() {
        let fetch: Fetcher = Arc::new(move |s, _t| {
            Box::pin(async move {
                tokio::time::sleep(Duration::from_millis(if s == "SLOW" { 2000 } else { 50 })).await;
                Some(vec![Bar { t: 1, o: 1.0, h: 1.0, l: 1.0, c: 1.0, v: 1.0 }])
            })
        });
        let intraday = Intraday::new(fetch);
        let (bars, loading) = intraday.get_waiting("SBIN", "1m", Duration::from_secs(1)).await;
        assert_eq!(bars.len(), 1);
        assert!(!loading);
        let (bars, loading) = intraday.get_waiting("SLOW", "1m", Duration::from_millis(100)).await;
        assert!(bars.is_empty() && loading);
    }
}

#[cfg(test)]
mod adjusted_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn adjusted() -> Adjusted {
        Adjusted { bars: vec![Bar { t: 1, o: 1.0, h: 1.0, l: 1.0, c: 1.0, v: 1.0 }], flags: vec![], joined: vec![], provenance: None }
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

    // ---- T6 review (R2): the adjusted-history fetch (helper, then the command run directly) is one deadline and one bounded admission ----
    fn py() -> Option<String> {
        ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists()).map(String::from)
    }
    fn helper_that(py: &str, on_request: &str) -> Arc<crate::helper::Helpers> {
        let code = format!("import json, sys, time\nprint(json.dumps({{\"ready\": True}}), flush=True)\nfor line in sys.stdin:\n    r = json.loads(line)\n{on_request}\n");
        let limits = crate::bounded::Limits { workers: 1, capacity: 2, deadline: Duration::from_secs(60), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 };
        Arc::new(crate::helper::Helpers::with_limits(vec![py.to_string(), "-c".into(), code], limits))
    }
    fn one_bar() -> Adjusted {
        Adjusted { bars: vec![Bar { t: 1, o: 1.0, h: 1.0, l: 1.0, c: 1.0, v: 1.0 }], flags: vec![], joined: vec![], provenance: None }
    }

    #[tokio::test]
    async fn an_adjusted_fetch_whose_helper_expired_does_not_run_the_command_beside_it() {
        let Some(py) = py() else { return };
        let helpers = helper_that(&py, "    time.sleep(60)");
        let adm = crate::bounded::Admission::new("t", 2, 2);
        let direct_runs = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counted = direct_runs.clone();
        let got = fetch_adjusted(Some(&helpers), &adm, vec![], Duration::from_millis(900), move |_permit, _left| async move {
            counted.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(one_bar())
        })
        .await;
        assert_eq!(got.is_none(), true);
        assert_eq!(direct_runs.load(std::sync::atomic::Ordering::SeqCst), 0, "the helper's job expired: nothing else may run the same work");
    }

    #[tokio::test]
    async fn the_direct_fetch_after_a_helper_failure_is_given_only_what_is_left_of_the_deadline() {
        let Some(py) = py() else { return };
        let helpers = helper_that(&py, "    time.sleep(0.6)\n    print(json.dumps({\"id\": r[\"id\"], \"ok\": False}), flush=True)");
        let adm = crate::bounded::Admission::new("t", 2, 2);
        let left_seen = Arc::new(Mutex::new(None));
        let seen = left_seen.clone();
        let got = fetch_adjusted(Some(&helpers), &adm, vec![], Duration::from_millis(2_000), move |_permit, budget| async move {
            *seen.lock().unwrap() = Some(budget.remaining());
            Some(one_bar())
        })
        .await;
        assert_eq!(got.is_some(), true, "a helper that failed leaves the command free to run");
        let left = left_seen.lock().unwrap().expect("the command was run");
        assert_eq!(left <= Duration::from_millis(1_400), true, "0.6 s of the 2 s went on the helper: {left:?}");
    }

    #[tokio::test]
    async fn a_full_admission_refuses_the_adjusted_fetch_at_once_without_running_anything() {
        let adm = crate::bounded::Admission::new("t", 1, 0);
        let _held = adm.admit("chart_bars", &crate::bounded::Budget::new(Duration::from_secs(5))).await.ok().expect("a free place");
        let ran = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counted = ran.clone();
        let t = std::time::Instant::now();
        let got = fetch_adjusted(None, &adm, vec![], Duration::from_secs(5), move |_permit, _| async move {
            counted.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(one_bar())
        })
        .await;
        assert_eq!((got.is_none(), ran.load(std::sync::atomic::Ordering::SeqCst), t.elapsed() < Duration::from_millis(500)), (true, 0, true));
    }

    #[tokio::test]
    async fn an_adjusted_fetch_refused_by_a_full_helper_queue_does_not_run_the_command_either() {
        let Some(py) = py() else { return };
        let code = "import json, sys, time\nprint(json.dumps({\"ready\": True}), flush=True)\nfor line in sys.stdin:\n    time.sleep(60)\n";
        let limits = crate::bounded::Limits { workers: 1, capacity: 0, deadline: Duration::from_secs(60), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 };
        let helpers = Arc::new(crate::helper::Helpers::with_limits(vec![py, "-c".into(), code.into()], limits));
        let busy = tokio::spawn({ let h = helpers.clone(); async move { h.run(crate::helper::HelperOp::ChartBars, &[], Duration::from_millis(1_500)).await } });
        tokio::time::sleep(Duration::from_millis(400)).await; // the only helper has it
        let adm = crate::bounded::Admission::new("t", 2, 2);
        let ran = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counted = ran.clone();
        let t = std::time::Instant::now();
        let got = fetch_adjusted(Some(&helpers), &adm, vec![], Duration::from_secs(5), move |_permit, _| async move {
            counted.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(one_bar())
        })
        .await;
        assert_eq!((got.is_none(), ran.load(std::sync::atomic::Ordering::SeqCst), t.elapsed() < Duration::from_millis(900)), (true, 0, true), "refused at once, and nothing else ran the work");
        let _ = busy.await;
    }

    #[tokio::test]
    async fn the_helper_is_given_what_is_left_after_the_wait_for_a_place_in_the_adjusted_fetch() {
        let Some(py) = py() else { return };
        let helpers = helper_that(&py, "    time.sleep(60)");
        let adm = Arc::new(crate::bounded::Admission::new("t", 1, 1));
        let held = adm.admit("chart_bars", &crate::bounded::Budget::new(Duration::from_secs(30))).await.ok().expect("a free place");
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(600)).await;
            drop(held);
        });
        let ran = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counted = ran.clone();
        let t = std::time::Instant::now();
        let got = fetch_adjusted(Some(&helpers), &adm, vec![], Duration::from_millis(1_400), move |_permit, _| async move {
            counted.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Some(one_bar())
        })
        .await;
        assert_eq!(got.is_none(), true);
        assert_eq!(t.elapsed() > Duration::from_millis(1_100) && t.elapsed() < Duration::from_millis(1_900), true, "0.6 s waiting and the helper's time are one 1.4 s deadline, not 0.6 s and then a fresh 1.4 s: {:?}", t.elapsed());
        assert_eq!(ran.load(std::sync::atomic::Ordering::SeqCst), 0, "the helper expired: the command was not run beside it");
    }

    // ---- T6 review 2: the command run directly has an owner of its own and the fetch's deadline ----
    #[tokio::test]
    async fn the_adjusted_command_that_overruns_is_cut_at_the_deadline_it_was_given_and_ended() {
        let Some(py) = py() else { return };
        let adm = crate::bounded::Admission::new("t", 1, 0);
        let permit = adm.admit("chart_bars", &crate::bounded::Budget::new(Duration::from_secs(5))).await.ok().expect("a place");
        let mut cmd = tokio::process::Command::new(py);
        cmd.args(["-c", "import time\ntime.sleep(60)"]);
        let t = std::time::Instant::now();
        // (the test's own bound: a command given no real deadline fails this assertion instead of waiting a minute)
        let got = tokio::time::timeout(Duration::from_secs(8), run_chart_bars(&adm, permit, cmd, &crate::bounded::Budget::new(Duration::from_millis(500)))).await;
        assert_eq!(got.is_ok(), true, "it came back at its deadline");
        assert_eq!(got.unwrap().is_none(), true);
        assert_eq!(t.elapsed() < Duration::from_secs(3), true, "{:?}", t.elapsed());
    }

    // ---- T6 review 3: the deadline the fetch began with goes all the way down ----
    #[tokio::test]
    async fn the_adjusted_command_is_bound_by_the_fetchs_original_deadline_however_long_the_start_takes() {
        let Some(py) = py() else { return };
        let adm = Arc::new(crate::bounded::Admission::new("t", 1, 0));
        let owner = adm.clone();
        let t = std::time::Instant::now();
        let got = fetch_adjusted(None, &adm, vec![], Duration::from_millis(1_500), move |permit, budget| async move {
            tokio::time::sleep(Duration::from_millis(900)).await; // something slow between being given the time and starting the command
            let mut cmd = tokio::process::Command::new(py);
            cmd.args(["-c", "import time\ntime.sleep(60)"]);
            run_chart_bars(&owner, permit, cmd, &budget).await
        })
        .await;
        assert_eq!(got.is_none(), true);
        assert_eq!(t.elapsed() < Duration::from_millis(2_100), true, "one 1.5 s deadline, not 0.9 s and then a fresh 1.5 s: {:?}", t.elapsed());
    }
}
