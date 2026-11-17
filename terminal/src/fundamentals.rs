//! The Fundamentals page: one company's statements as the document `athena.fundamentals.page` builds, and its Excel workbook.
//! A page or valuation is asked of a warm Python helper (`crate::helper`, 0.1 to 0.3 s), or when the helper is certainly finished without an answer (it could not start, its command failed), of the module started for the request (a second
//! or two). A request has one deadline (T6): the wait for a place, the helper and the module draw on it, and a helper request that was refused, expired or not confirmed stopped is not followed by the module, with a price read from the warehouse's `screen` table. An answer is kept for `KEEP_FOR`, and for no longer than the next company fetch from the page.
//! The fundamentals file itself is only ever read here, never written. Nothing but a fixed message from a failed child reaches the page.
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};
use duckdb::{AccessMode, Config, Connection};
use serde_json::{json, Value};
use tokio::process::Command;
use crate::bounded::{AdmitError, Admission, Budget, DirectError, Permit};
use crate::helper::{Next, Why};

const LIMIT: Duration = Duration::from_secs(30);
/// Numbers the workbook files of every `FundamentalsJobs` in this process, so two of them (two tests, say) never share a file name.
static EXPORT_SEQUENCE: AtomicU64 = AtomicU64::new(0);
const AT_ONCE: usize = 2;
/// How many requests may wait for one of the `AT_ONCE` places (more are refused at once).
const WAITING: usize = 8;
/// How long a page or valuation is served from memory. The filings change only when a fetch runs; a fetch from the page clears the kept answers at once, and a
/// fetch run by hand shows within this time.
pub const KEEP_FOR: Duration = Duration::from_secs(10 * 60);

/// The reader name under which the cached closes are registered: its own, not `screen`, so that what the price cache serves can never overwrite what another reader of the `screen` table served.
pub const PRICE_READER: &str = "fundamentals_price";

/// (module, arguments, fetches finished when it was made) -> (when, the document)
type Kept = HashMap<(String, Vec<String>, u64), (Instant, Value)>;

/// Every stock's last close from the warehouse `screen` table, with what the answers made from it must say about themselves.
struct Closes {
    /// The modification times of the warehouse file and its write-ahead log when it was read.
    file: Option<SystemTime>,
    log: Option<SystemTime>,
    closes: HashMap<String, (f64, String)>,
    /// Which data it was read from (the real file, or a snapshot generation and why), as `snapshot::Provenance::to_value`: kept with the closes, so a price served from them later is described by the data it came from.
    provenance: Value,
    /// The newest day any held close is from: the date these closes stop at.
    latest_day: Option<String>,
    read_at: Instant,
}

/// What a read of the `screen` table gave: the closes, the provenance of the data read, and the newest close day.
type Read = (HashMap<String, (f64, String)>, Value, Option<String>);

/// The provenance of a price served from `held`: the data it was read from, the date the closes stop at (the manifest's per-dataset dates, when a snapshot, are kept beside it), and whether it came from the
/// cache and how old the held closes are. `at` is when the answer was given, which the registry stamps.
fn served_provenance(held: &Closes, hit: bool) -> Value {
    let mut p = held.provenance.clone();
    if let Some(o) = p.as_object_mut() {
        if let Some(manifest_dates) = o.remove("data_as_of").filter(|v| !v.is_null()) {
            o.insert("snapshot_data_as_of".into(), manifest_dates);
        }
        o.insert("data_as_of".into(), json!(held.latest_day));
        o.insert("cache".into(), json!({"hit": hit, "age_seconds": if hit { held.read_at.elapsed().as_secs() } else { 0 }}));
    }
    p
}

#[derive(Debug, PartialEq, Eq)]
pub enum FundError {
    BadSymbol,
    BadBasis,
    /// The store holds nothing for this symbol and basis.
    NoData,
    /// The store is locked by the ingest, or missing.
    Busy,
    /// Every place is taken and every waiting place too, or a helper's queue is full: refused at once, nothing was started.
    Overloaded,
    /// The request's one deadline (admission wait, helper and command together) passed.
    TimedOut,
    Failed(String),
}

/// What a request is told when its helper request was stopped (refused, expired, held, unconfirmed or shutting down) rather than answered.
fn stopped(why: Why, what: &str) -> FundError {
    match why {
        Why::Overloaded => FundError::Overloaded,
        Why::TimedOut => FundError::TimedOut,
        Why::Held => FundError::Failed(format!("the helper for the {what} is being held until its process is confirmed stopped; see /api/work")),
        Why::ShuttingDown => FundError::Failed("the terminal is shutting down".into()),
        Why::MayHaveRun => FundError::Failed(format!("the helper for the {what} ended without an answer after the request was sent; it is not repeated because it may already have run")),
        Why::Unconfirmed => FundError::Failed(format!("the helper for the {what} failed and its process could not be confirmed stopped; see /api/work")),
    }
}

/// What a refused or expired admission is to the caller.
fn admit_error(e: AdmitError) -> FundError {
    match e {
        AdmitError::Full { .. } => FundError::Overloaded,
        AdmitError::Expired => FundError::TimedOut,
        AdmitError::Held => FundError::Failed("every place is held by a command that could not be confirmed stopped; see /api/work".into()),
        AdmitError::Closed => FundError::Failed("the terminal is shutting down".into()),
    }
}

/// An NSE symbol as the page and the ingest write it: capitals, digits, `&` and `-`.
pub fn symbol_ok(symbol: &str) -> bool {
    symbol.chars().next().is_some_and(|c| c != '-') && symbol.len() <= 20 && symbol.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '&' || c == '-')
}

pub fn basis_ok(basis: &str) -> bool {
    basis == "consolidated" || basis == "standalone"
}

pub struct FundamentalsJobs {
    /// Program and leading arguments; the symbol and options follow.
    page: Vec<String>,
    export: Vec<String>,
    /// The valuation module (`athena.valuation.page`); empty when it is not configured.
    valuation: Vec<String>,
    warehouse: PathBuf,
    store: PathBuf,
    admission: Arc<Admission>,
    /// The one deadline of a request: admission wait, the helper and the command run directly all draw on it.
    limit: Duration,
    helpers: Option<Arc<crate::helper::Helpers>>,
    /// Counts the company fetches finished from the page; an answer made before the latest one is not served.
    fetches: Arc<AtomicU64>,
    kept: std::sync::Mutex<Kept>,
    /// All the closes at once, read when the warehouse changes: opening a 400 MB file for every page asked cost far more than the page.
    closes: std::sync::Mutex<Option<Closes>>,
    /// The name under which the cached closes are registered for `/api/data-source` (see `PRICE_READER`).
    price_reader: String,
}

impl FundamentalsJobs {
    pub fn new(page: Vec<String>, export: Vec<String>, warehouse: PathBuf, store: PathBuf) -> Self {
        FundamentalsJobs {
            page, export, valuation: Vec::new(), warehouse, store, admission: Arc::new(Admission::new("fundamentals", AT_ONCE, WAITING)), limit: LIMIT, helpers: None,
            fetches: Arc::new(AtomicU64::new(0)), kept: std::sync::Mutex::new(HashMap::new()), closes: std::sync::Mutex::new(None), price_reader: PRICE_READER.to_string(),
        }
    }

    /// Keep the children held by direct commands in a file, so that a restart knows about them (see `bounded.rs`).
    pub fn with_journal(mut self, path: PathBuf) -> Self {
        self.admission = Arc::new(Admission::with_journal("fundamentals", AT_ONCE, WAITING, path));
        self
    }

    /// Register the cached closes under another name (tests share one process-wide registry and each names its own reader).
    #[cfg(test)]
    pub fn with_price_reader(mut self, name: &str) -> Self {
        self.price_reader = name.to_string();
        self
    }

    /// The deadline of one request (the default is 30 s).
    #[cfg(test)]
    pub fn with_limit(mut self, limit: Duration) -> Self {
        self.limit = limit;
        self
    }

    /// How many requests run at once and how many may wait (the defaults are 2 and 8).
    #[cfg(test)]
    pub fn with_admission(mut self, running: usize, waiting: usize) -> Self {
        self.admission = Arc::new(Admission::new("fundamentals", running, waiting));
        self
    }

    /// The admission as the status route sees it.
    pub fn admission_view(&self) -> Arc<dyn crate::bounded::PoolView> {
        self.admission.view()
    }

    pub fn with_helpers(mut self, helpers: Arc<crate::helper::Helpers>) -> Self {
        self.helpers = Some(helpers);
        self
    }

    /// Share the count of finished company fetches (`FundFetch::finished`).
    pub fn with_fetches(mut self, fetches: Arc<AtomicU64>) -> Self {
        self.fetches = fetches;
        self
    }

    pub fn with_valuation(mut self, valuation: Vec<String>) -> Self {
        self.valuation = valuation;
        self
    }

    pub fn standard(python: &str, warehouse: PathBuf, store: PathBuf) -> Self {
        let module = |m: &str| vec![python.to_string(), "-m".into(), m.into()];
        Self::new(module("athena.fundamentals.page"), module("athena.fundamentals.export"), warehouse, store).with_valuation(module("athena.valuation.page"))
    }

    /// The last close the warehouse holds for a stock and a note saying which day it is from; `None` for an index or an unknown symbol.
    ///
    /// What is registered (`/api/data-source`, under its own reader name, never `screen`) is what is **served**, as `SearchCache` does: every price returned, a cache hit included, records the provenance of
    /// the closes it came from (renewing the entry), and a refresh that fails while closes are held is recorded apart as a refresh failure and never written over what is served. A symbol the closes do not
    /// hold returns nothing and registers no answer.
    pub fn price(&self, symbol: &str) -> Option<(f64, String)> {
        let stamp = |p: &Path| std::fs::metadata(p).and_then(|m| m.modified()).ok();
        let wal = PathBuf::from(format!("{}.wal", self.warehouse.display()));
        let (file, log) = (stamp(&self.warehouse), stamp(&wal));
        let mut cache = self.closes.lock().unwrap();
        let stale = match &*cache {
            Some(c) => c.file != file || c.log != log,
            None => true,
        };
        let mut read_now = false;
        if stale {
            // the warehouse changed (or was never read): read every close once. If it cannot be opened now (a build is writing it) the closes already held are served, not nothing.
            match self.read_closes() {
                Ok((closes, provenance, latest_day)) => {
                    crate::snapshot::clear_refresh_failure(&self.price_reader);
                    *cache = Some(Closes { file, log, closes, provenance, latest_day, read_at: Instant::now() });
                    read_now = true;
                }
                Err(failure) => {
                    crate::snapshot::record_refresh_failure(&self.price_reader, failure.clone());
                    if cache.is_none() {
                        crate::snapshot::record_served(&self.price_reader, None, failure); // nothing is held and nothing could be read: what was served is no data, and why
                    }
                }
            }
        }
        let held = cache.as_ref()?;
        let (close, note) = held.closes.get(symbol)?;
        crate::snapshot::record_served(&self.price_reader, None, served_provenance(held, !read_now));
        Some((*close, note.clone()))
    }

    /// Every stock's last close and the day it is from, straight from the warehouse, with the provenance of what was read; the failure (as the registry reports it) when the file cannot be opened or its
    /// `screen` table cannot be read. A missing, zero or non-finite close is left out. Opens without registering: `price` registers what it serves.
    fn read_closes(&self) -> Result<Read, Value> {
        let opened = crate::snapshot::open_unrecorded(&self.warehouse).map_err(|f| f.to_value())?;
        let (conn, provenance) = (opened.conn, opened.provenance.to_value());
        let mut stmt = conn.prepare("SELECT symbol, close, CAST(data_as_of AS VARCHAR) FROM screen").map_err(|_| crate::snapshot::unreadable_content("the screen table could not be prepared"))?;
        let rows = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<f64>>(1)?, r.get::<_, Option<String>>(2)?)))
            .map_err(|_| crate::snapshot::unreadable_content("the screen table could not be read"))?;
        let mut out = HashMap::new();
        let mut latest: Option<String> = None;
        for row in rows.flatten() {
            if let (symbol, Some(close), day) = row {
                if close.is_finite() && close > 0.0 {
                    if let Some(d) = day.as_ref().filter(|d| !d.is_empty()) {
                        if latest.as_ref().map_or(true, |l| d > l) {
                            latest = Some(d.clone());
                        }
                    }
                    out.insert(symbol, (close, format!("warehouse close {}", day.unwrap_or_default())));
                }
            }
        }
        Ok((out, provenance, latest))
    }

    /// The arguments after the module: the symbol, the basis, the store and the warehouse price when there is one.
    fn args(&self, symbol: &str, basis: &str) -> Vec<String> {
        let mut args = vec![symbol.to_string(), "--basis".into(), basis.to_string(), "--store".into(), self.store.to_string_lossy().into_owned()];
        if let Some((close, note)) = self.price(symbol) {
            args.extend(["--price".into(), close.to_string(), "--price-note".into(), note]);
        }
        args
    }

    fn command(&self, base: &[String], symbol: &str, basis: &str) -> Command {
        let mut cmd = Command::new(&base[0]);
        cmd.args(&base[1..]).args(self.args(symbol, basis));
        cmd.stdin(Stdio::null()).stderr(Stdio::null()).kill_on_drop(true);
        cmd
    }

    /// A page or valuation document: kept, else from a helper, else from the module started for it. `what` names it in the messages.
    async fn document(&self, base: &[String], symbol: &str, basis: &str, what: &str) -> Result<Value, FundError> {
        let module = base.last().cloned().unwrap_or_default();
        let op = if module.ends_with("valuation.page") { crate::helper::HelperOp::Valuation } else { crate::helper::HelperOp::Page };
        let args = self.args(symbol, basis);
        let key = (module, args.clone(), self.fetches.load(Ordering::Relaxed));
        if let Some((at, doc)) = self.kept.lock().unwrap().get(&key) {
            if at.elapsed() < KEEP_FOR {
                return Ok(doc.clone());
            }
        }
        // one deadline for everything below: the wait for a place, the helper and the module run directly all draw on it
        let budget = Budget::new(self.limit);
        let mut permit = Some(self.admission.admit(op.kind(), &budget).await.map_err(admit_error)?);
        let stdout = match &self.helpers {
            Some(h) => match h.run_within(op, &args, &budget).await.next(op.idempotent()) {
                Next::Output(out) => out,
                // only a helper that is certainly finished with the work leaves room for the module to be run once
                Next::RunCommand => self.run_module(base, symbol, basis, what, &budget, permit.take().expect("held until here"), op.kind()).await?,
                // refused, expired or not confirmed gone: the work may still be going or was never accepted, and running it again beside it is not the answer
                Next::Stop(why) => return Err(stopped(why, what)),
            },
            None => self.run_module(base, symbol, basis, what, &budget, permit.take().expect("held until here"), op.kind()).await?,
        };
        let doc: Value = serde_json::from_slice(&stdout).map_err(|_| FundError::Failed(format!("the {what} gave an unreadable answer")))?;
        if doc["busy"] == true {
            return Err(FundError::Busy);
        }
        if doc["error"] == "no statements stored" {
            return Err(FundError::NoData);
        }
        let mut kept = self.kept.lock().unwrap();
        kept.retain(|_, (at, _)| at.elapsed() < KEEP_FOR);
        kept.insert(key, (Instant::now(), doc.clone()));
        Ok(doc)
    }

    /// The module started for the request, given only what is left of the request's deadline.
    async fn run_module(&self, base: &[String], symbol: &str, basis: &str, what: &str, budget: &Budget, permit: Permit, kind: &'static str) -> Result<Vec<u8>, FundError> {
        if budget.expired() {
            return Err(FundError::TimedOut);
        }
        // the command has an owner of its own: if the deadline passes it is ended and confirmed gone before its place is free, whatever becomes of this request
        let output = self.admission.run_command(permit, self.command(base, symbol, basis), kind, budget).await.map_err(|e| match e {
            DirectError::Timeout => FundError::TimedOut,
            DirectError::Spawn => FundError::Failed(format!("could not start the {what}")),
            DirectError::Shutdown => FundError::Failed("the terminal is shutting down".into()),
        })?;
        if !output.success {
            return Err(FundError::Failed(format!("the {what} failed")));
        }
        Ok(output.stdout)
    }

    fn check(symbol: &str, basis: &str) -> Result<(), FundError> {
        if !symbol_ok(symbol) {
            return Err(FundError::BadSymbol);
        }
        if !basis_ok(basis) {
            return Err(FundError::BadBasis);
        }
        Ok(())
    }

    /// The page document for one company.
    pub async fn page(&self, symbol: &str, basis: &str) -> Result<Value, FundError> {
        Self::check(symbol, basis)?;
        self.document(&self.page, symbol, basis, "page builder").await
    }

    /// The valuation document for one company (`athena.valuation.page`): a value range, the cost of capital and the assumptions behind them.
    pub async fn valuation(&self, symbol: &str, basis: &str) -> Result<Value, FundError> {
        Self::check(symbol, basis)?;
        if self.valuation.is_empty() {
            return Err(FundError::Failed("the valuation module is not configured".into()));
        }
        self.document(&self.valuation, symbol, basis, "valuation").await
    }

    /// The workbook for one company, as the bytes of an `.xlsx` file.
    pub async fn export(&self, symbol: &str, basis: &str) -> Result<Vec<u8>, FundError> {
        Self::check(symbol, basis)?;
        let budget = Budget::new(self.limit);
        let permit = self.admission.admit("export", &budget).await.map_err(admit_error)?;
        let file = std::env::temp_dir().join(format!("athena-fund-{}-{}.xlsx", std::process::id(), EXPORT_SEQUENCE.fetch_add(1, Ordering::Relaxed)));
        let mut cmd = self.command(&self.export, symbol, basis);
        cmd.arg("--out").arg(&file);
        let ran = self.admission.run_command(permit, cmd, "export", &budget).await;
        let result = match ran {
            Err(DirectError::Timeout) => Err(FundError::TimedOut),
            Err(DirectError::Spawn) => Err(FundError::Failed("could not start the export".into())),
            Err(DirectError::Shutdown) => Err(FundError::Failed("the terminal is shutting down".into())),
            Ok(out) if out.success => std::fs::read(&file).map_err(|_| FundError::Failed("the export left no file".into())),
            Ok(out) if out.code == Some(2) => Err(FundError::NoData),
            Ok(_) => Err(FundError::Failed("the export failed".into())),
        };
        let _ = std::fs::remove_file(&file);
        result
    }

    pub fn store_path(&self) -> &Path {
        &self.store
    }

    /// How much is stored: companies, periods and the newest period end. Read straight from the store; a locked file is retried briefly.
    pub fn status(&self) -> Result<Value, FundError> {
        status_of(&self.store)
    }
}

fn status_of(path: &Path) -> Result<Value, FundError> {
    if !path.exists() {
        return Err(FundError::NoData);
    }
    for attempt in 0..4 {
        if attempt > 0 {
            std::thread::sleep(Duration::from_millis(300));
        }
        let Ok(config) = Config::default().access_mode(AccessMode::ReadOnly) else { continue };
        let Ok(conn) = Connection::open_with_flags(path, config) else { continue }; // locked by the ingest: try again shortly
        return conn
            .query_row(
                "SELECT count(DISTINCT symbol), count(DISTINCT (symbol, basis, period_start, period_end)), CAST(max(period_end) AS VARCHAR) FROM statement_lines",
                [],
                |r| Ok(json!({"companies": r.get::<_, i64>(0)?, "periods": r.get::<_, i64>(1)?, "latest": r.get::<_, Option<String>>(2)?})),
            )
            .map_err(|_| FundError::Failed("the store could not be read".into()));
    }
    Err(FundError::Busy)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn python() -> Option<String> {
        ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| Path::new(p).exists()).map(String::from)
    }

    /// A stand-in for the page module: prints the arguments it received as JSON, or what the symbol asks for.
    fn fake(script: &str, warehouse: PathBuf) -> Option<FundamentalsJobs> {
        let prefix = vec![python()?, "-c".into(), script.into()];
        Some(FundamentalsJobs::new(prefix.clone(), prefix, warehouse, PathBuf::from("no/such.duckdb")))
    }

    const ECHO: &str = r#"
import json, sys
a = sys.argv[1:]
if a[0] == "MISSING":
    print(json.dumps({"symbol": "MISSING", "error": "no statements stored"}))
elif a[0] == "LOCKED":
    print(json.dumps({"symbol": "LOCKED", "error": "busy", "busy": True}))
elif a[0] == "BROKEN":
    sys.exit(1)
elif a[0] == "GARBAGE":
    print("not json")
else:
    out = a[a.index("--out") + 1] if "--out" in a else None
    if out:
        open(out, "wb").write(b"PK-fake-workbook")
    else:
        print(json.dumps({"args": a}))
"#;

    fn warehouse(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("athena-fund-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("w.duckdb");
        Connection::open(&path)
            .unwrap()
            .execute_batch(
                "CREATE TABLE screen (symbol VARCHAR, close DOUBLE, data_as_of DATE);
                 INSERT INTO screen VALUES ('TCS', 3200.5, DATE '2026-10-16'), ('ZERO', 0.0, DATE '2026-10-16'), ('NOPRICE', NULL, DATE '2026-10-16');",
            )
            .unwrap();
        path
    }

    #[test]
    fn symbols_and_bases_are_checked() {
        assert!(symbol_ok("TCS") && symbol_ok("M&M") && symbol_ok("BAJAJ-AUTO"));
        for bad in ["", "tcs", "A B", "A;B", "../x", "-h", "--version", &"X".repeat(21)] {
            assert!(!symbol_ok(bad), "{bad}");
        }
        assert!(basis_ok("consolidated") && basis_ok("standalone"));
        assert!(!basis_ok("both") && !basis_ok("") && !basis_ok("Consolidated"));
    }

    #[test]
    fn closes_are_read_again_when_the_warehouse_changes_and_kept_when_it_cannot_be_opened() {
        let path = warehouse("cache");
        let jobs = FundamentalsJobs::new(vec![], vec![], path.clone(), PathBuf::from("x"));
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        std::thread::sleep(Duration::from_millis(50));
        Connection::open(&path).unwrap().execute_batch("UPDATE screen SET close = 3300.0 WHERE symbol = 'TCS'; CHECKPOINT;").unwrap();
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3300.0), "a changed warehouse is read again");
        std::fs::remove_file(&path).unwrap();
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3300.0), "the closes held are served when the file cannot be opened");
        assert_eq!(jobs.price("ZERO"), None);
    }

    #[test]
    fn the_price_is_the_warehouse_close_and_a_missing_or_zero_one_is_none() {
        let jobs = FundamentalsJobs::new(vec![], vec![], warehouse("price"), PathBuf::from("x"));
        assert_eq!(jobs.price("TCS"), Some((3200.5, "warehouse close 2026-10-16".to_string())));
        for none in ["ZERO", "NOPRICE", "UNKNOWN"] {
            assert_eq!(jobs.price(none), None, "{none}");
        }
        assert_eq!(FundamentalsJobs::new(vec![], vec![], PathBuf::from("no/such.duckdb"), PathBuf::from("x")).price("TCS"), None);
    }

    #[tokio::test]
    async fn the_child_gets_the_symbol_basis_store_and_price_and_its_document_comes_back() {
        let Some(jobs) = fake(ECHO, warehouse("args")) else { return };
        let doc = jobs.page("TCS", "standalone").await.unwrap();
        let args: Vec<String> = doc["args"].as_array().unwrap().iter().map(|a| a.as_str().unwrap().to_string()).collect();
        assert_eq!(&args[..3], ["TCS", "--basis", "standalone"]);
        assert!(args.windows(2).any(|w| w == ["--price", "3200.5"]));
        assert!(args.contains(&"warehouse close 2026-10-16".to_string()));
    }

    #[tokio::test]
    async fn no_price_is_passed_when_the_warehouse_has_none() {
        let Some(jobs) = fake(ECHO, warehouse("noprice")) else { return };
        let doc = jobs.page("ZERO", "consolidated").await.unwrap();
        assert!(!doc["args"].to_string().contains("--price"));
    }

    #[tokio::test]
    async fn bad_input_never_starts_a_child() {
        let jobs = FundamentalsJobs::new(vec!["no-such-program-xyz".into()], vec!["no-such-program-xyz".into()], PathBuf::from("w"), PathBuf::from("s"));
        assert_eq!(jobs.page("a;b", "consolidated").await, Err(FundError::BadSymbol));
        assert_eq!(jobs.page("TCS", "x").await, Err(FundError::BadBasis));
        assert_eq!(jobs.export("../x", "consolidated").await, Err(FundError::BadSymbol));
        assert_eq!(jobs.page("TCS", "consolidated").await, Err(FundError::Failed("could not start the page builder".into())));
    }

    #[tokio::test]
    async fn the_childs_failures_become_fixed_errors() {
        let Some(jobs) = fake(ECHO, warehouse("fail")) else { return };
        assert_eq!(jobs.page("MISSING", "consolidated").await, Err(FundError::NoData));
        assert_eq!(jobs.page("LOCKED", "consolidated").await, Err(FundError::Busy));
        assert_eq!(jobs.page("BROKEN", "consolidated").await, Err(FundError::Failed("the page builder failed".into())));
        assert_eq!(jobs.page("GARBAGE", "consolidated").await, Err(FundError::Failed("the page builder gave an unreadable answer".into())));
    }

    #[tokio::test]
    async fn the_valuation_runs_its_own_module_with_the_same_arguments_and_is_refused_when_not_configured() {
        let Some(jobs) = fake(ECHO, warehouse("valuation")) else { return };
        assert_eq!(jobs.valuation("TCS", "consolidated").await, Err(FundError::Failed("the valuation module is not configured".into())));
        let prefix = vec![python().unwrap(), "-c".into(), ECHO.into()];
        let jobs = jobs.with_valuation(prefix);
        let doc = jobs.valuation("TCS", "standalone").await.unwrap();
        let args: Vec<String> = doc["args"].as_array().unwrap().iter().map(|a| a.as_str().unwrap().to_string()).collect();
        assert_eq!(&args[..3], ["TCS", "--basis", "standalone"]);
        assert!(args.windows(2).any(|w| w == ["--price", "3200.5"]));
        assert_eq!(jobs.valuation("MISSING", "consolidated").await, Err(FundError::NoData));
        assert_eq!(jobs.valuation("a;b", "consolidated").await, Err(FundError::BadSymbol));
        assert_eq!(jobs.valuation("BROKEN", "consolidated").await, Err(FundError::Failed("the valuation failed".into())));
    }

    #[tokio::test]
    async fn an_export_returns_the_files_bytes_and_removes_the_file() {
        let Some(jobs) = fake(ECHO, warehouse("export")) else { return };
        assert_eq!(jobs.export("TCS", "consolidated").await.unwrap(), b"PK-fake-workbook");
        let left: Vec<_> = std::fs::read_dir(std::env::temp_dir()).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().starts_with(&format!("athena-fund-{}-", std::process::id()))).collect();
        assert!(left.is_empty(), "the temporary workbook is removed");
        assert_eq!(jobs.export("BROKEN", "consolidated").await, Err(FundError::Failed("the export failed".into())));
    }

    // ---- T5-F1: the price cache registers what it serves (its own reader), keeps the provenance of the data it holds, and keeps refresh failures apart ----
    use crate::snapshot::testkit::{publish, ruin_main, temp_dir};

    /// A warehouse (real DuckDB file, synthetic rows) whose `screen` table holds TCS at `close` and INFY.
    fn put_screen(path: &Path, close: f64, day: &str) {
        Connection::open(path)
            .unwrap()
            .execute_batch(&format!(
                "DROP TABLE IF EXISTS screen; CREATE TABLE screen (symbol VARCHAR, close DOUBLE, data_as_of DATE);
                 INSERT INTO screen VALUES ('TCS', {close}, DATE '{day}'), ('INFY', 1500.0, DATE '{day}'); CHECKPOINT;"
            ))
            .unwrap();
    }

    /// (what was last served under `reader`, its refresh failure, the whole report), from the process-wide registry the endpoint reads.
    fn registered(reader: &str) -> (Value, Value, Value) {
        let s = crate::snapshot::data_sources();
        (s["readers"][reader].clone(), s["refresh_failures"][reader].clone(), s)
    }

    fn jobs_for(path: &Path, reader: &str) -> FundamentalsJobs {
        FundamentalsJobs::new(vec![], vec![], path.to_path_buf(), PathBuf::from("x")).with_price_reader(reader)
    }

    #[test]
    fn a_price_from_the_real_file_is_registered_under_its_own_reader_and_serving_it_again_renews_the_entry() {
        let dir = temp_dir("fp-main");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        let jobs = jobs_for(&main, "fp-main");
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let (first, refresh, _) = registered("fp-main");
        assert_eq!((first["source"].clone(), first["reader"].clone(), first["recent"].clone()), (json!("main"), json!("fp-main"), json!(true)));
        assert_eq!((first["cache"]["hit"].clone(), first["cache"]["age_seconds"].clone()), (json!(false), json!(0)), "read just now, not from the cache");
        assert_eq!(first["data_as_of"], "2026-10-16", "the date the closes it holds stop at");
        assert!(refresh.is_null());
        std::thread::sleep(Duration::from_millis(1100));
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5), "a cache hit: the warehouse did not change");
        let (hit, _, _) = registered("fp-main");
        assert_eq!(hit["generation_id"], first["generation_id"], "the same data");
        assert_eq!(hit["cache"]["hit"], true);
        assert!(hit["cache"]["age_seconds"].as_u64().unwrap() >= 1, "how old the held closes are: {hit}");
        assert!(hit["at"].as_u64().unwrap() > first["at"].as_u64().unwrap(), "a cache hit is a served answer and renews the entry");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_default_reader_is_not_screen_and_a_symbol_the_cache_does_not_hold_is_not_registered_as_served() {
        let dir = temp_dir("fp-default");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        let jobs = FundamentalsJobs::new(vec![], vec![], main.clone(), PathBuf::from("x")); // the name production uses
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let s = crate::snapshot::data_sources();
        assert_eq!(s["readers"][PRICE_READER]["source"], "main");
        assert!(s["readers"].get("screen").is_none(), "the price cache does not register as `screen`: {s}");
        assert_ne!(PRICE_READER, "screen");
        let other = jobs_for(&main, "fp-nosym");
        assert_eq!(other.price("UNKNOWN"), None);
        assert!(registered("fp-nosym").0.is_null(), "nothing was served from the closes: no answer is claimed");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn cached_prices_after_a_refresh_failure_keep_serving_with_their_own_provenance_and_the_failure_is_kept_apart() {
        let dir = temp_dir("fp-retained");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        let jobs = jobs_for(&main, "fp-retained");
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let (built, _, _) = registered("fp-retained");
        ruin_main(&main); // the warehouse changes and cannot be read, and no snapshot exists
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5), "the retained close is still returned");
        let (served, refresh, _) = registered("fp-retained");
        assert_eq!((served["source"].clone(), served["generation_id"].clone()), (built["source"].clone(), built["generation_id"].clone()), "what was served is the data the cache was read from");
        assert!(served.get("failure").is_none(), "the failed refresh is not the provenance of the price served: {served}");
        assert_eq!(served["cache"]["hit"], true, "served from what was held");
        assert_eq!((refresh["failure"]["main_reason"].clone(), refresh["failure"]["snapshot_reason"].clone()), (json!("main_unreadable"), json!("absent")), "both reasons kept");
        assert!(served["at"].as_u64().unwrap() >= refresh["at"].as_u64().unwrap(), "the answer was given after the refresh failed");
        // a symbol the held closes do not have, asked while the refresh keeps failing: no price, and the registry still describes the data that was served, not the failed refresh
        assert_eq!(jobs.price("NOSUCH"), None);
        let (after, refresh_again, _) = registered("fp-retained");
        assert!(after.get("failure").is_none() && after["generation_id"] == built["generation_id"], "the failed refresh is not registered as the answer: {after}");
        assert!(refresh_again["at"].as_u64().unwrap() >= refresh["at"].as_u64().unwrap() && refresh_again.is_object());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_cache_first_read_from_a_snapshot_is_served_with_the_snapshots_generation_and_dates_and_stays_so_on_a_hit() {
        let dir = temp_dir("fp-snapshot");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        let gen = publish(&main, 3);
        ruin_main(&main); // a build holds or has damaged the real file: the closes come from the generation
        let jobs = jobs_for(&main, "fp-snapshot");
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let (first, refresh, _) = registered("fp-snapshot");
        assert_eq!((first["source"].clone(), first["generation_id"].clone(), first["fallback_reason"].clone()), (json!("snapshot"), json!(gen), json!("main_unreadable")));
        assert_eq!(first["snapshot_data_as_of"]["daily_bars"], "2026-11-13", "the manifest's dates are kept beside the answer's own date");
        assert_eq!(first["data_as_of"], "2026-10-16");
        assert!(refresh.is_null(), "reading from a snapshot is not a failed refresh");
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let (hit, _, _) = registered("fp-snapshot");
        assert_eq!((hit["generation_id"].clone(), hit["cache"]["hit"].clone()), (json!(gen), json!(true)));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn with_nothing_held_and_nothing_readable_there_is_no_price_and_the_registry_says_why() {
        let dir = temp_dir("fp-none");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        ruin_main(&main);
        let jobs = jobs_for(&main, "fp-none");
        assert_eq!(jobs.price("TCS"), None);
        let (served, refresh, _) = registered("fp-none");
        assert_eq!((served["failure"]["main_reason"].clone(), served["failure"]["snapshot_reason"].clone()), (json!("main_unreadable"), json!("absent")), "what was served is no data, and why");
        assert_eq!(refresh["failure"]["main_reason"], "main_unreadable");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_later_good_read_clears_the_failure_and_serves_the_new_data() {
        let dir = temp_dir("fp-recovers");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        let jobs = jobs_for(&main, "fp-recovers");
        jobs.price("TCS").unwrap();
        let (built, _, _) = registered("fp-recovers");
        ruin_main(&main);
        jobs.price("TCS").unwrap();
        assert!(registered("fp-recovers").1.is_object(), "failing now");
        std::fs::remove_file(&main).unwrap();
        put_screen(&main, 3300.0, "2026-10-17"); // the build finished: a readable file again
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3300.0));
        let (served, refresh, _) = registered("fp-recovers");
        assert!(refresh.is_null(), "recovered: nothing is failing any more");
        assert_ne!(served["generation_id"], built["generation_id"], "the new file, not the old one");
        assert_eq!((served["cache"]["hit"].clone(), served["data_as_of"].clone()), (json!(false), json!("2026-10-17")));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_screen_reader_and_the_cached_prices_are_separate_entries_and_differing_generations_are_reported() {
        let dir = temp_dir("fp-separate");
        let main = dir.join("w.duckdb");
        put_screen(&main, 3200.5, "2026-10-16");
        let gen = publish(&main, 3);
        let jobs = jobs_for(&main, "fp-sep-price");
        jobs.price("TCS").unwrap(); // the cache holds closes from the real file
        let before = registered("fp-sep-price").0;
        ruin_main(&main);
        let screen = crate::snapshot::open(&main, "fp-sep-screen").ok().expect("a snapshot answers the screen"); // another reader of the same table, now on a generation
        drop(screen);
        let (price, _, s) = registered("fp-sep-price");
        let screen = s["readers"]["fp-sep-screen"].clone();
        assert_eq!((price["generation_id"].clone(), price["source"].clone()), (before["generation_id"].clone(), json!("main")), "the screen's answer did not overwrite the price's");
        assert_eq!((screen["generation_id"].clone(), screen["source"].clone()), (json!(gen), json!("snapshot")));
        let recent: Vec<&str> = s["recent_generations"].as_array().unwrap().iter().filter_map(|g| g.as_str()).collect();
        assert!(recent.contains(&gen.as_str()) && recent.contains(&price["generation_id"].as_str().unwrap()), "both generations are reported: {recent:?}");
        assert_eq!(s["mixed_generations"], true);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_status_counts_what_is_stored_and_a_missing_store_is_no_data() {
        let dir = std::env::temp_dir().join(format!("athena-fund-status-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("f.duckdb");
        Connection::open(&path)
            .unwrap()
            .execute_batch(
                "CREATE TABLE statement_lines (symbol TEXT, basis TEXT, statement TEXT, period_start DATE, period_end DATE, key TEXT, value DOUBLE);
                 INSERT INTO statement_lines VALUES ('TCS','consolidated','income',DATE '2025-04-01',DATE '2026-03-31','sales',1),
                   ('TCS','consolidated','income',DATE '2025-04-01',DATE '2026-03-31','pbt',1),
                   ('TCS','consolidated','income',DATE '2026-04-01',DATE '2026-06-30','sales',1),
                   ('SBIN','consolidated','income',DATE '2025-04-01',DATE '2026-03-31','sales',1);",
            )
            .unwrap();
        let s = status_of(&path).unwrap();
        assert_eq!((s["companies"].as_i64(), s["periods"].as_i64(), s["latest"].as_str()), (Some(2), Some(3), Some("2026-06-30")));
        assert_eq!(status_of(&dir.join("none.duckdb")), Err(FundError::NoData));
    }

    // ---- T6 review (R2): one deadline and one bounded admission for the helper, the command run directly and the wait for a place ----
    use crate::bounded::Limits;

    /// A helper (a Python child speaking JSON lines) that is ready at once and then does what `on_request` says; it runs here instead of the real one.
    fn helper_with(py: &str, on_request: &str, workers: usize, capacity: usize) -> Arc<crate::helper::Helpers> {
        let code = format!("import json, sys, time\nprint(json.dumps({{\"ready\": True}}), flush=True)\nfor line in sys.stdin:\n    r = json.loads(line)\n{on_request}\n");
        let limits = Limits { workers, capacity, deadline: Duration::from_secs(60), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 };
        Arc::new(crate::helper::Helpers::with_limits(vec![py.to_string(), "-c".into(), code], limits))
    }
    const HANGS: &str = "    time.sleep(60)";
    const FAILS_AFTER_HALF_A_SECOND: &str = "    time.sleep(0.6)\n    print(json.dumps({\"id\": r[\"id\"], \"ok\": False, \"error\": \"x\"}), flush=True)";

    /// The module run directly: counts its starts in `counter`, sleeps, prints a page.
    fn counting_module(py: &str, counter: &Path, sleep_secs: f64) -> Vec<String> {
        let code = format!("import json, time\nopen(r'{}', 'a').write('x\\n')\ntime.sleep({sleep_secs})\nprint(json.dumps({{\"ok\": 1}}))\n", counter.display());
        vec![py.to_string(), "-c".into(), code]
    }
    fn starts(counter: &Path) -> usize {
        std::fs::read_to_string(counter).map(|t| t.lines().count()).unwrap_or(0)
    }
    fn counter_file(tag: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("athena-fund-{tag}-{}.count", std::process::id()));
        let _ = std::fs::remove_file(&p);
        p
    }

    #[tokio::test]
    async fn a_request_whose_helper_expired_does_not_start_the_module_a_second_time() {
        let Some(py) = python() else { return };
        let counter = counter_file("expired");
        let module = counting_module(&py, &counter, 0.0);
        let jobs = FundamentalsJobs::new(module.clone(), module, warehouse("expired"), PathBuf::from("no/such.duckdb")).with_helpers(helper_with(&py, HANGS, 1, 2)).with_limit(Duration::from_millis(900));
        let t = std::time::Instant::now();
        assert_eq!(jobs.page("TCS", "consolidated").await, Err(FundError::TimedOut));
        assert_eq!(t.elapsed() < Duration::from_millis(1_800), true, "{:?}", t.elapsed());
        tokio::time::sleep(Duration::from_millis(1_200)).await;
        assert_eq!(starts(&counter), 0, "the helper's job had expired: running the module beside it would duplicate work that may still be going");
    }

    #[tokio::test]
    async fn a_request_refused_by_a_full_helper_queue_does_not_start_the_module_and_is_told_it_is_overloaded() {
        let Some(py) = python() else { return };
        let counter = counter_file("refused");
        let module = counting_module(&py, &counter, 0.0);
        let jobs = Arc::new(FundamentalsJobs::new(module.clone(), module, warehouse("refused"), PathBuf::from("no/such.duckdb")).with_helpers(helper_with(&py, HANGS, 1, 0)).with_limit(Duration::from_millis(1_500)));
        let first = tokio::spawn({ let j = jobs.clone(); async move { j.page("TCS", "consolidated").await } });
        tokio::time::sleep(Duration::from_millis(400)).await; // the only helper is busy with it
        let t = std::time::Instant::now();
        assert_eq!(jobs.page("INFY", "consolidated").await, Err(FundError::Overloaded));
        assert_eq!(t.elapsed() < Duration::from_millis(500), true, "refused at once: {:?}", t.elapsed());
        assert_eq!(first.await.unwrap(), Err(FundError::TimedOut));
        tokio::time::sleep(Duration::from_millis(500)).await;
        assert_eq!(starts(&counter), 0, "neither the refusal nor the expiry started the module");
    }

    #[tokio::test]
    async fn waiting_for_a_place_counts_against_the_deadline_and_the_wait_itself_is_bounded() {
        let Some(py) = python() else { return };
        let counter = counter_file("admission");
        let module = counting_module(&py, &counter, 1.0);
        let jobs = Arc::new(FundamentalsJobs::new(module.clone(), module, warehouse("admission"), PathBuf::from("no/such.duckdb")).with_admission(1, 1).with_limit(Duration::from_millis(1_800)));
        let a = tokio::spawn({ let j = jobs.clone(); async move { j.page("AAA", "consolidated").await } });
        tokio::time::sleep(Duration::from_millis(100)).await;
        let t = std::time::Instant::now();
        let b = tokio::spawn({ let j = jobs.clone(); async move { (j.page("BBB", "consolidated").await, t.elapsed()) } });
        tokio::time::sleep(Duration::from_millis(100)).await;
        let t3 = std::time::Instant::now();
        assert_eq!(jobs.page("CCC", "consolidated").await, Err(FundError::Overloaded), "the one waiting place is taken");
        assert_eq!(t3.elapsed() < Duration::from_millis(500), true, "refused at once: {:?}", t3.elapsed());
        assert_eq!(a.await.unwrap().is_ok(), true, "the request that held the place finished");
        let (b_result, b_took) = b.await.unwrap();
        assert_eq!(b_result, Err(FundError::TimedOut), "B waited for A, so the module it then started had only what was left of B's own 1.8 s");
        assert_eq!(b_took > Duration::from_millis(1_500) && b_took < Duration::from_millis(2_300), true, "ended near its own deadline, not after waiting and then running a full limit again: {b_took:?}");
    }

    #[tokio::test]
    async fn the_module_run_after_a_helper_failure_gets_only_what_is_left_of_the_deadline() {
        let Some(py) = python() else { return };
        let counter = counter_file("left");
        let module = counting_module(&py, &counter, 3.0);
        let jobs = FundamentalsJobs::new(module.clone(), module, warehouse("left"), PathBuf::from("no/such.duckdb")).with_helpers(helper_with(&py, FAILS_AFTER_HALF_A_SECOND, 1, 2)).with_limit(Duration::from_millis(1_400));
        let t = std::time::Instant::now();
        assert_eq!(jobs.page("TCS", "consolidated").await, Err(FundError::TimedOut));
        assert_eq!(t.elapsed() < Duration::from_millis(2_000), true, "the helper's 0.6 s and the module's wait are one deadline of 1.4 s, not 0.6 + 1.4 s: {:?}", t.elapsed());
        assert_eq!(starts(&counter) <= 1, true, "a helper that failed leaves the module free to be run once");
    }

    #[tokio::test]
    async fn a_request_that_waits_past_its_deadline_for_a_place_gives_up_at_the_deadline_and_does_not_run_on_past_it() {
        let Some(py) = python() else { return };
        let counter = counter_file("waits-out");
        let module = counting_module(&py, &counter, 3.0);
        let jobs = Arc::new(FundamentalsJobs::new(module.clone(), module, warehouse("waits-out"), PathBuf::from("no/such.duckdb")).with_admission(1, 1).with_limit(Duration::from_millis(1_200)));
        let a = tokio::spawn({ let j = jobs.clone(); async move { j.page("AAA", "consolidated").await } });
        tokio::time::sleep(Duration::from_millis(150)).await; // A holds the only place for about 3 s, and has 1.2 s itself
        let t = std::time::Instant::now();
        assert_eq!(jobs.page("BBB", "consolidated").await, Err(FundError::TimedOut), "B's whole 1.2 s went on waiting");
        assert_eq!(t.elapsed() > Duration::from_millis(1_000) && t.elapsed() < Duration::from_millis(1_900), true, "it gave up at its own deadline, not when A finally finished: {:?}", t.elapsed());
        assert_eq!(a.await.unwrap(), Err(FundError::TimedOut), "A's own module was cut at A's deadline");
        // (B may start the module in the last fraction of its own second, once A's place is freed; what it may not do is run on past its deadline, which the timing above shows)
        assert_eq!(starts(&counter) <= 2, true, "A started the module, and B at most once");
    }

    #[test]
    fn a_stopped_helper_request_is_told_what_stopped_it_and_never_as_a_success() {
        assert_eq!(stopped(Why::Overloaded, "page builder"), FundError::Overloaded);
        assert_eq!(stopped(Why::TimedOut, "page builder"), FundError::TimedOut);
        for (why, words) in [(Why::Held, "being held"), (Why::Unconfirmed, "could not be confirmed stopped"), (Why::ShuttingDown, "shutting down"), (Why::MayHaveRun, "may already have run")] {
            let shown = stopped(why, "valuation");
            assert_eq!(matches!(shown, FundError::Failed(_)), true, "{why:?} must be a failure, not a success or a timeout: {shown:?}");
            let FundError::Failed(m) = shown else { continue };
            assert_eq!(m.contains(words), true, "{why:?}: {m}");
        }
    }

    #[tokio::test]
    async fn the_wait_for_a_place_is_cut_at_the_requests_own_deadline_even_when_the_place_is_held_far_longer() {
        let Some(py) = python() else { return };
        let counter = counter_file("held-place");
        let module = counting_module(&py, &counter, 0.0);
        let jobs = FundamentalsJobs::new(module.clone(), module, warehouse("held-place"), PathBuf::from("no/such.duckdb")).with_admission(1, 1).with_limit(Duration::from_millis(1_000));
        // somebody else holds the only place for 4 s, which is much longer than this request is allowed to wait
        let held = jobs.admission.admit("page", &Budget::new(Duration::from_secs(30))).await.ok().expect("a free place");
        let t = std::time::Instant::now();
        // (the test's own bound: a request that waits for a place that is never freed fails this assertion instead of hanging the test)
        let got = tokio::time::timeout(Duration::from_secs(5), jobs.page("TCS", "consolidated")).await;
        assert_eq!(got, Ok(Err(FundError::TimedOut)));
        assert_eq!(t.elapsed() > Duration::from_millis(800) && t.elapsed() < Duration::from_millis(1_800), true, "it gave up at its own 1 s, not when the place was freed: {:?}", t.elapsed());
        drop(held);
        assert_eq!(starts(&counter), 0, "it never started the module");
    }

    #[tokio::test]
    async fn the_helper_is_given_what_is_left_after_the_wait_for_a_place_not_the_whole_limit() {
        let Some(py) = python() else { return };
        let counter = counter_file("helper-left");
        let module = counting_module(&py, &counter, 0.0);
        let jobs = Arc::new(FundamentalsJobs::new(module.clone(), module, warehouse("helper-left"), PathBuf::from("no/such.duckdb")).with_helpers(helper_with(&py, HANGS, 2, 4)).with_admission(1, 1).with_limit(Duration::from_millis(1_200)));
        let first = tokio::spawn({ let j = jobs.clone(); async move { j.page("AAA", "consolidated").await } });
        tokio::time::sleep(Duration::from_millis(200)).await; // A has the place and its helper hangs until A's own deadline
        let t = std::time::Instant::now();
        let second = jobs.page("BBB", "consolidated").await;
        assert_eq!(second, Err(FundError::TimedOut));
        assert_eq!(t.elapsed() > Duration::from_millis(900) && t.elapsed() < Duration::from_millis(1_700), true, "B's 1.2 s covered its wait for A and its helper's time, so it ended near 1.2 s, not after a fresh 1.2 s for the helper: {:?}", t.elapsed());
        assert_eq!(first.await.unwrap(), Err(FundError::TimedOut));
        assert_eq!(starts(&counter), 0, "the module was never started beside a helper that expired");
    }

    // ---- T6 review 2: the module run directly has an owner of its own and its place is the owner's until it is confirmed gone ----
    #[tokio::test]
    async fn a_module_that_overran_its_deadline_is_really_gone_and_its_place_is_free_afterwards() {
        use crate::bounded::PoolView;
        let Some(py) = python() else { return };
        let beat = counter_file("heartbeat");
        let code = format!("import time\nwhile True:\n    open(r'{}', 'a').write('x')\n    time.sleep(0.05)\n", beat.display());
        let module = vec![py, "-c".into(), code];
        let jobs = FundamentalsJobs::new(module.clone(), module, warehouse("heartbeat"), PathBuf::from("no/such.duckdb")).with_admission(1, 0).with_limit(Duration::from_millis(700));
        assert_eq!(jobs.page("TCS", "consolidated").await, Err(FundError::TimedOut));
        let mut freed = false;
        for _ in 0..100 {
            if jobs.admission.admit("page", &Budget::new(Duration::from_millis(10))).await.is_ok() {
                freed = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert_eq!(freed, true, "the place came back once the exit was confirmed: {}", jobs.admission.status());
        let size = std::fs::metadata(&beat).map(|m| m.len()).unwrap_or(0);
        tokio::time::sleep(Duration::from_millis(400)).await;
        assert_eq!(std::fs::metadata(&beat).map(|m| m.len()).unwrap_or(0), size, "the module is not writing any more");
    }

    // ---- T6 review 2: the export runs under its owner too; what its exit code says is kept ----
    #[tokio::test]
    async fn an_export_that_exits_with_code_2_is_no_data_and_one_that_fails_is_a_failure_and_a_good_one_is_read() {
        let Some(py) = python() else { return };
        let run = |code: &str| {
            let export = vec![py.clone(), "-c".into(), code.to_string()];
            FundamentalsJobs::new(vec![], export, warehouse("export-codes"), PathBuf::from("no/such.duckdb")).with_limit(Duration::from_secs(20))
        };
        assert_eq!(run("import sys\nsys.exit(2)").export("TCS", "consolidated").await, Err(FundError::NoData));
        assert_eq!(run("import sys\nsys.exit(1)").export("TCS", "consolidated").await, Err(FundError::Failed("the export failed".into())));
        let good = "import sys\nout = sys.argv[sys.argv.index('--out') + 1]\nopen(out, 'wb').write(b'PK-fake')";
        assert_eq!(run(good).export("TCS", "consolidated").await, Ok(b"PK-fake".to_vec()));
    }
}
