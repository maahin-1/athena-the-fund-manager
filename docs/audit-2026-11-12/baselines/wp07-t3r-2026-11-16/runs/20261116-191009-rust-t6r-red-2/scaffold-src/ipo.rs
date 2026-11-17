//! The IPO tab: the document `athena.ipo.page` builds from the store the `ipo` job keeps (open, coming, closed and listed issues, and the drafts filed with SEBI). It reads a local file and no
//! network, so an answer is kept for one minute only. One at a time; nothing but a fixed message from a failed child reaches the page.
use std::path::PathBuf;
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::process::Command;
use tokio::sync::Semaphore;

const LIMIT: Duration = Duration::from_secs(60);
const KEEP: Duration = Duration::from_secs(60);

#[derive(Debug, PartialEq, Eq)]
pub enum IpoError {
    Busy,
    Failed(String),
}

pub struct IpoJobs {
    /// Program and leading arguments.
    module: Vec<String>,
    warehouse: PathBuf,
    slot: Semaphore,
    kept: std::sync::Mutex<Option<(Instant, Value)>>,
}

impl IpoJobs {
    pub fn new(module: Vec<String>, warehouse: PathBuf) -> Self {
        IpoJobs { module, warehouse, slot: Semaphore::new(1), kept: std::sync::Mutex::new(None) }
    }

    pub fn standard(python: &str, warehouse: PathBuf) -> Self {
        Self::new(vec![python.to_string(), "-m".into(), "athena.ipo.page".into()], warehouse)
    }

    pub async fn page(&self) -> Result<Value, IpoError> {
        if let Some((at, doc)) = self.kept.lock().unwrap().as_ref() {
            if at.elapsed() < KEEP {
                return Ok(doc.clone());
            }
        }
        let _slot = self.slot.try_acquire().map_err(|_| IpoError::Busy)?;
        let program = self.module.first().ok_or_else(|| IpoError::Failed("the IPO page is not configured".into()))?;
        let mut cmd = Command::new(program);
        cmd.args(&self.module[1..]).arg("--warehouse").arg(&self.warehouse).stdin(Stdio::null()).stderr(Stdio::null()).stdout(Stdio::piped()).kill_on_drop(true);
        let output = tokio::time::timeout(LIMIT, async { cmd.spawn()?.wait_with_output().await })
            .await
            .map_err(|_| IpoError::Failed("the IPO page took longer than a minute and was stopped".into()))?
            .map_err(|_| IpoError::Failed("could not start the IPO page".into()))?;
        if !output.status.success() {
            return Err(IpoError::Failed("the IPO page failed".into()));
        }
        let doc: Value = serde_json::from_slice(&output.stdout).map_err(|_| IpoError::Failed("the IPO page gave an unreadable answer".into()))?;
        *self.kept.lock().unwrap() = Some((Instant::now(), doc.clone()));
        Ok(doc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with(script: &str) -> Option<IpoJobs> {
        std::process::Command::new("sh").arg("-c").arg("true").status().ok()?;
        Some(IpoJobs::new(vec!["sh".into(), "-c".into(), script.into(), "page".into()], PathBuf::from("w.duckdb")))
    }

    #[tokio::test]
    async fn the_warehouse_path_reaches_the_command_and_an_answer_is_kept_for_a_minute() {
        let dir = std::env::temp_dir().join(format!("athena-ipo-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let log = dir.join("calls").to_string_lossy().replace('\\', "/");
        let _ = std::fs::remove_file(&log);
        let Some(jobs) = with(&format!("echo \"$@\" >> '{log}'; echo '{{\"open\": []}}'")) else { return };
        assert_eq!(jobs.page().await.unwrap()["open"], serde_json::json!([]));
        assert_eq!(jobs.page().await.unwrap()["open"], serde_json::json!([]));
        let calls = std::fs::read_to_string(&log).unwrap();
        assert_eq!(calls.lines().count(), 1, "the second answer is the kept one");
        assert!(calls.starts_with("--warehouse w.duckdb"), "{calls}");
    }

    #[tokio::test]
    async fn a_failing_child_and_a_garbled_answer_are_told_apart() {
        let Some(bad) = with("exit 3") else { return };
        assert_eq!(bad.page().await, Err(IpoError::Failed("the IPO page failed".into())));
        assert_eq!(with("echo nonsense").unwrap().page().await, Err(IpoError::Failed("the IPO page gave an unreadable answer".into())));
    }
}
