//! The Sentiment page: the document `athena.sentiment.page` builds for one stock (the exchange's filings, the press, Reddit when the owner has set it up, and on request a model's
//! reading of the news). The command goes to the network, so an answer is kept for ten minutes (a reading, which costs a model call, for thirty). Nothing but a fixed message from a
//! failed child reaches the page.
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::process::Command;
use tokio::sync::Semaphore;

const LIMIT: Duration = Duration::from_secs(150);
const KEEP_PAGE: Duration = Duration::from_secs(10 * 60);
const KEEP_TONE: Duration = Duration::from_secs(30 * 60);
const AT_ONCE: usize = 2;

#[derive(Debug, PartialEq, Eq)]
pub enum SentimentError {
    BadSymbol,
    Busy,
    Failed(String),
}

pub struct SentimentJobs {
    /// Program and leading arguments; the symbol and options follow.
    module: Vec<String>,
    warehouse: PathBuf,
    slots: Semaphore,
    kept: std::sync::Mutex<HashMap<(String, bool), (Instant, Value)>>,
}

impl SentimentJobs {
    pub fn new(module: Vec<String>, warehouse: PathBuf) -> Self {
        SentimentJobs { module, warehouse, slots: Semaphore::new(AT_ONCE), kept: std::sync::Mutex::new(HashMap::new()) }
    }

    pub fn standard(python: &str, warehouse: PathBuf) -> Self {
        Self::new(vec![python.to_string(), "-m".into(), "athena.sentiment.page".into()], warehouse)
    }

    pub async fn page(&self, symbol: &str, tone: bool) -> Result<Value, SentimentError> {
        if !crate::fundamentals::symbol_ok(symbol) {
            return Err(SentimentError::BadSymbol);
        }
        let key = (symbol.to_string(), tone);
        if let Some((at, doc)) = self.kept.lock().unwrap().get(&key) {
            if at.elapsed() < if tone { KEEP_TONE } else { KEEP_PAGE } {
                return Ok(doc.clone());
            }
        }
        let _slot = self.slots.try_acquire().map_err(|_| SentimentError::Busy)?;
        let program = self.module.first().ok_or_else(|| SentimentError::Failed("the sentiment page is not configured".into()))?;
        let mut cmd = Command::new(program);
        cmd.args(&self.module[1..]).arg(symbol);
        if tone {
            cmd.arg("--tone");
        }
        cmd.arg("--warehouse").arg(&self.warehouse).stdin(Stdio::null()).stderr(Stdio::null()).stdout(Stdio::piped()).kill_on_drop(true);
        let output = tokio::time::timeout(LIMIT, async { cmd.spawn()?.wait_with_output().await })
            .await
            .map_err(|_| SentimentError::Failed("the sentiment page took longer than two and a half minutes and was stopped".into()))?
            .map_err(|_| SentimentError::Failed("could not start the sentiment page".into()))?;
        if !output.status.success() {
            return Err(SentimentError::Failed("the sentiment page failed".into()));
        }
        let doc: Value = serde_json::from_slice(&output.stdout).map_err(|_| SentimentError::Failed("the sentiment page gave an unreadable answer".into()))?;
        if doc.get("error").is_some() {
            return Err(SentimentError::BadSymbol);
        }
        let mut kept = self.kept.lock().unwrap();
        kept.retain(|k, (at, _)| at.elapsed() < if k.1 { KEEP_TONE } else { KEEP_PAGE });
        kept.insert(key, (Instant::now(), doc.clone()));
        Ok(doc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with(script: &str) -> Option<SentimentJobs> {
        std::process::Command::new("sh").arg("-c").arg("true").status().ok()?;
        Some(SentimentJobs::new(vec!["sh".into(), "-c".into(), script.into(), "page".into()], PathBuf::from("w.duckdb")))
    }

    #[tokio::test]
    async fn the_symbol_and_the_tone_flag_reach_the_command_and_an_answer_is_kept() {
        let dir = std::env::temp_dir().join(format!("athena-sentiment-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let log = dir.join("calls").to_string_lossy().replace('\\', "/");
        let _ = std::fs::remove_file(&log);
        // $1 is the symbol; the arguments after it are logged, and the stand-in answers with the symbol it was given
        let Some(jobs) = with(&format!("echo \"$@\" >> '{log}'; echo \"{{\\\"symbol\\\": \\\"$1\\\"}}\"")) else { return };
        assert_eq!(jobs.page("TCS", false).await.unwrap()["symbol"], "TCS");
        assert_eq!(jobs.page("TCS", false).await.unwrap()["symbol"], "TCS");
        assert_eq!(jobs.page("TCS", true).await.unwrap()["symbol"], "TCS");
        let calls = std::fs::read_to_string(&log).unwrap();
        let lines: Vec<&str> = calls.lines().collect();
        assert_eq!(lines.len(), 2, "the second plain answer is the kept one; the reading is a separate request");
        assert!(lines[0].starts_with("TCS --warehouse") && lines[1].starts_with("TCS --tone --warehouse"), "{lines:?}");
    }

    #[tokio::test]
    async fn a_bad_symbol_a_failing_child_and_a_garbled_answer_are_told_apart() {
        let Some(ok) = with("echo '{}'") else { return };
        assert_eq!(ok.page("a;b", false).await, Err(SentimentError::BadSymbol));
        assert_eq!(ok.page("tcs", false).await, Err(SentimentError::BadSymbol));
        assert_eq!(with("exit 3").unwrap().page("TCS", false).await, Err(SentimentError::Failed("the sentiment page failed".into())));
        assert_eq!(with("echo nonsense").unwrap().page("TCS", false).await, Err(SentimentError::Failed("the sentiment page gave an unreadable answer".into())));
        assert_eq!(with("echo '{\"error\": \"bad symbol\"}'").unwrap().page("TCS", false).await, Err(SentimentError::BadSymbol));
    }
}
