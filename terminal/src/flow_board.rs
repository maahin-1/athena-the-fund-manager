//! The futures and options board: the document `athena.flow.board` builds from the derivatives history, asked of a warm Python helper or, when none can answer, of the
//! module started for the request. The history is end-of-day and changes once a day, so the answer is kept for `KEEP_FOR` and dropped at once when the file changes.
//! The derivatives file is only ever read, by the child. Nothing but a fixed message from a failed child reaches the page.
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};

use serde_json::Value;
use tokio::process::Command;
use tokio::sync::Semaphore;

const LIMIT: Duration = Duration::from_secs(60);
pub const KEEP_FOR: Duration = Duration::from_secs(5 * 60);

#[derive(Debug, PartialEq, Eq)]
pub enum BoardError {
    /// No derivatives history is stored yet.
    NoData,
    /// The file is being written by a load.
    Busy,
    Failed(String),
}

pub struct FlowBoard {
    /// Program and leading arguments; `--store PATH` follows.
    module: Vec<String>,
    store: PathBuf,
    helpers: Option<Arc<crate::helper::Helpers>>,
    slot: Semaphore,
    kept: std::sync::Mutex<Option<(Instant, Option<SystemTime>, Value)>>,
}

impl FlowBoard {
    pub fn new(module: Vec<String>, store: PathBuf) -> Self {
        FlowBoard { module, store, helpers: None, slot: Semaphore::new(1), kept: std::sync::Mutex::new(None) }
    }

    pub fn standard(python: &str, store: PathBuf) -> Self {
        Self::new(vec![python.to_string(), "-m".into(), "athena.flow.board".into()], store)
    }

    pub fn with_helpers(mut self, helpers: Arc<crate::helper::Helpers>) -> Self {
        self.helpers = Some(helpers);
        self
    }

    fn args(&self) -> Vec<String> {
        vec!["--store".into(), self.store.to_string_lossy().into_owned()]
    }

    fn modified(&self) -> Option<SystemTime> {
        std::fs::metadata(&self.store).and_then(|m| m.modified()).ok()
    }

    /// The board: kept, else from a helper, else from the module started for it.
    pub async fn board(&self) -> Result<Value, BoardError> {
        let modified = self.modified();
        if let Some((at, when, doc)) = self.kept.lock().unwrap().as_ref() {
            if at.elapsed() < KEEP_FOR && *when == modified {
                return Ok(doc.clone());
            }
        }
        let _slot = self.slot.acquire().await.map_err(|_| BoardError::Busy)?;
        let args = self.args();
        let from_helper = match &self.helpers {
            Some(h) => h.run("flow_board", &args, LIMIT).await,
            None => None,
        };
        let stdout = match from_helper {
            Some(out) => out,
            None => {
                let mut cmd = Command::new(self.module.first().ok_or_else(|| BoardError::Failed("the board is not configured".into()))?);
                cmd.args(&self.module[1..]).args(&args).stdin(Stdio::null()).stderr(Stdio::null()).stdout(Stdio::piped()).kill_on_drop(true);
                let output = tokio::time::timeout(LIMIT, async { cmd.spawn()?.wait_with_output().await })
                    .await
                    .map_err(|_| BoardError::Failed("the board took longer than a minute and was stopped".into()))?
                    .map_err(|_| BoardError::Failed("could not start the board".into()))?;
                if !output.status.success() {
                    return Err(BoardError::Failed("the board failed".into()));
                }
                output.stdout
            }
        };
        let doc: Value = serde_json::from_slice(&stdout).map_err(|_| BoardError::Failed("the board gave an unreadable answer".into()))?;
        if doc["busy"] == true {
            return Err(BoardError::Busy);
        }
        if doc.get("error").is_some() {
            return Err(BoardError::NoData);
        }
        *self.kept.lock().unwrap() = Some((Instant::now(), modified, doc.clone()));
        Ok(doc)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn board_with(script: &str, store: PathBuf) -> Option<FlowBoard> {
        // a shell that prints what the test says stands in for the Python module; skipped where there is no `sh`
        std::process::Command::new("sh").arg("-c").arg("true").status().ok()?;
        Some(FlowBoard::new(vec!["sh".into(), "-c".into(), script.into(), "board".into()], store))
    }

    #[tokio::test]
    async fn the_document_is_read_from_the_child_and_kept_until_the_file_changes() {
        let dir = std::env::temp_dir().join(format!("athena-flow-board-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let store = dir.join("d.duckdb");
        std::fs::write(&store, b"x").unwrap();
        let counter = dir.join("calls");
        let _ = std::fs::remove_file(&counter);
        let script = format!("echo x >> '{}'; echo '{{\"day\": \"2026-10-23\", \"rows\": [], \"events\": {{}}, \"universe\": 0}}'", counter.to_string_lossy().replace('\\', "/"));
        let Some(board) = board_with(&script, store.clone()) else { return };
        let first = board.board().await.unwrap();
        assert_eq!(first["day"], "2026-10-23");
        board.board().await.unwrap();
        let calls = |p: &PathBuf| std::fs::read_to_string(p).map(|t| t.lines().count()).unwrap_or(0);
        assert_eq!(calls(&counter), 1, "the second answer is the kept one");
        std::thread::sleep(Duration::from_millis(30));
        std::fs::write(&store, b"changed").unwrap(); // a load wrote the file
        board.board().await.unwrap();
        assert_eq!(calls(&counter), 2, "a changed file is read again");
    }

    #[tokio::test]
    async fn a_missing_history_a_busy_file_and_a_broken_child_are_told_apart() {
        let store = std::env::temp_dir().join("athena-flow-board-none.duckdb");
        let Some(none) = board_with("echo '{\"error\": \"no derivatives history is stored\"}'", store.clone()) else { return };
        assert_eq!(none.board().await, Err(BoardError::NoData));
        let busy = board_with("echo '{\"busy\": true, \"error\": \"IOException\"}'", store.clone()).unwrap();
        assert_eq!(busy.board().await, Err(BoardError::Busy));
        let garbled = board_with("echo 'not json'", store.clone()).unwrap();
        assert_eq!(garbled.board().await, Err(BoardError::Failed("the board gave an unreadable answer".into())));
        let failing = board_with("exit 3", store).unwrap();
        assert_eq!(failing.board().await, Err(BoardError::Failed("the board failed".into())));
    }
}
