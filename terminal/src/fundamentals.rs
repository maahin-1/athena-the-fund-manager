//! The Fundamentals page: one company's statements as the document `athena.fundamentals.page` builds, and its Excel workbook.
//! Each request starts the Python module once (a second or two; they are rare), with a price read from the warehouse's `screen` table.
//! The fundamentals file itself is only ever read here, never written. Nothing but a fixed message from a failed child reaches the page.
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use duckdb::{AccessMode, Config, Connection, OptionalExt};
use serde_json::{json, Value};
use tokio::process::Command;
use tokio::sync::Semaphore;

const LIMIT: Duration = Duration::from_secs(30);
const AT_ONCE: usize = 2;

#[derive(Debug, PartialEq, Eq)]
pub enum FundError {
    BadSymbol,
    BadBasis,
    /// The store holds nothing for this symbol and basis.
    NoData,
    /// The store is locked by the ingest, or missing.
    Busy,
    Failed(String),
}

/// An NSE symbol as the page and the ingest write it: capitals, digits, `&` and `-`.
pub fn symbol_ok(symbol: &str) -> bool {
    !symbol.is_empty() && symbol.len() <= 20 && symbol.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '&' || c == '-')
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
    slots: Semaphore,
    counter: AtomicU64,
}

impl FundamentalsJobs {
    pub fn new(page: Vec<String>, export: Vec<String>, warehouse: PathBuf, store: PathBuf) -> Self {
        FundamentalsJobs { page, export, valuation: Vec::new(), warehouse, store, slots: Semaphore::new(AT_ONCE), counter: AtomicU64::new(0) }
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
    pub fn price(&self, symbol: &str) -> Option<(f64, String)> {
        let config = Config::default().access_mode(AccessMode::ReadOnly).ok()?;
        let conn = Connection::open_with_flags(&self.warehouse, config).ok()?;
        let (close, day): (Option<f64>, Option<String>) = conn
            .query_row("SELECT close, CAST(data_as_of AS VARCHAR) FROM screen WHERE symbol = ?", [symbol], |r| Ok((r.get(0)?, r.get(1)?)))
            .optional()
            .ok()??;
        let close = close.filter(|c| c.is_finite() && *c > 0.0)?;
        Some((close, format!("warehouse close {}", day.unwrap_or_default())))
    }

    fn command(&self, base: &[String], symbol: &str, basis: &str) -> Command {
        let mut cmd = Command::new(&base[0]);
        cmd.args(&base[1..]).arg(symbol).args(["--basis", basis]).args(["--store"]).arg(&self.store);
        if let Some((close, note)) = self.price(symbol) {
            cmd.args(["--price", &close.to_string(), "--price-note", &note]);
        }
        cmd.stdin(Stdio::null()).stderr(Stdio::null()).kill_on_drop(true);
        cmd
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
        let _slot = self.slots.acquire().await.map_err(|_| FundError::Busy)?;
        let mut cmd = self.command(&self.page, symbol, basis);
        cmd.stdout(Stdio::piped());
        let output = tokio::time::timeout(LIMIT, async { cmd.spawn()?.wait_with_output().await })
            .await
            .map_err(|_| FundError::Failed("the page took longer than 30 seconds and was stopped".into()))?
            .map_err(|_| FundError::Failed("could not start the page builder".into()))?;
        if !output.status.success() {
            return Err(FundError::Failed("the page builder failed".into()));
        }
        let doc: Value = serde_json::from_slice(&output.stdout).map_err(|_| FundError::Failed("the page builder gave an unreadable answer".into()))?;
        if doc["busy"] == true {
            return Err(FundError::Busy);
        }
        if doc["error"] == "no statements stored" {
            return Err(FundError::NoData);
        }
        Ok(doc)
    }

    /// The valuation document for one company (`athena.valuation.page`): a value range, the cost of capital and the assumptions behind them.
    pub async fn valuation(&self, symbol: &str, basis: &str) -> Result<Value, FundError> {
        Self::check(symbol, basis)?;
        if self.valuation.is_empty() {
            return Err(FundError::Failed("the valuation module is not configured".into()));
        }
        let _slot = self.slots.acquire().await.map_err(|_| FundError::Busy)?;
        let mut cmd = self.command(&self.valuation, symbol, basis);
        cmd.stdout(Stdio::piped());
        let output = tokio::time::timeout(LIMIT, async { cmd.spawn()?.wait_with_output().await })
            .await
            .map_err(|_| FundError::Failed("the valuation took longer than 30 seconds and was stopped".into()))?
            .map_err(|_| FundError::Failed("could not start the valuation".into()))?;
        if !output.status.success() {
            return Err(FundError::Failed("the valuation failed".into()));
        }
        let doc: Value = serde_json::from_slice(&output.stdout).map_err(|_| FundError::Failed("the valuation gave an unreadable answer".into()))?;
        if doc["busy"] == true {
            return Err(FundError::Busy);
        }
        if doc["error"] == "no statements stored" {
            return Err(FundError::NoData);
        }
        Ok(doc)
    }

    /// The workbook for one company, as the bytes of an `.xlsx` file.
    pub async fn export(&self, symbol: &str, basis: &str) -> Result<Vec<u8>, FundError> {
        Self::check(symbol, basis)?;
        let _slot = self.slots.acquire().await.map_err(|_| FundError::Busy)?;
        let file = std::env::temp_dir().join(format!("athena-fund-{}-{}.xlsx", std::process::id(), self.counter.fetch_add(1, Ordering::Relaxed)));
        let mut cmd = self.command(&self.export, symbol, basis);
        cmd.arg("--out").arg(&file).stdout(Stdio::null());
        let waited = tokio::time::timeout(LIMIT, async { cmd.spawn()?.wait().await }).await;
        let result = match waited {
            Err(_) => Err(FundError::Failed("the export took longer than 30 seconds and was stopped".into())),
            Ok(Err(_)) => Err(FundError::Failed("could not start the export".into())),
            Ok(Ok(status)) if status.success() => std::fs::read(&file).map_err(|_| FundError::Failed("the export left no file".into())),
            Ok(Ok(status)) if status.code() == Some(2) => Err(FundError::NoData),
            Ok(Ok(_)) => Err(FundError::Failed("the export failed".into())),
        };
        let _ = std::fs::remove_file(&file);
        result
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
        for bad in ["", "tcs", "A B", "A;B", "../x", &"X".repeat(21)] {
            assert!(!symbol_ok(bad), "{bad}");
        }
        assert!(basis_ok("consolidated") && basis_ok("standalone"));
        assert!(!basis_ok("both") && !basis_ok("") && !basis_ok("Consolidated"));
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
}
