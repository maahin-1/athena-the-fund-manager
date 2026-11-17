//! The futures and options board: the document `athena.flow.board` builds from the derivatives history, asked of a warm Python helper or, when the helper is certainly finished without an answer, of the
//! module started for the request; one deadline covers the wait for the place, the helper and the module, and a refused, expired or unconfirmed helper request is not followed by the module (T6). The history is end-of-day and changes once a day, so the answer is kept for `KEEP_FOR` and dropped at once when the file changes.
//! The derivatives file is only ever read, by the child. Nothing but a fixed message from a failed child reaches the page.
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};

use serde_json::Value;
use tokio::process::Command;
use crate::bounded::{AdmitError, Admission, Budget, DirectError, Permit};
use crate::helper::{Next, Why};

const LIMIT: Duration = Duration::from_secs(60);
pub const KEEP_FOR: Duration = Duration::from_secs(6 * 3600); // the history is end-of-day; a change to the file or its log drops the answer at once, so a long keep costs nothing

#[derive(Debug, PartialEq, Eq)]
pub enum BoardError {
    /// No derivatives history is stored yet.
    NoData,
    /// The file is being written by a load.
    Busy,
    /// The place is taken and every waiting place too, or the helper's queue is full: refused at once, nothing was started.
    Overloaded,
    /// The request's one deadline (admission wait, helper and command together) passed.
    TimedOut,
    Failed(String),
}

/// What a request is told when its helper request was stopped (refused, expired, held, unconfirmed or shutting down) rather than answered.
fn stopped(why: Why) -> BoardError {
    match why {
        Why::Overloaded => BoardError::Overloaded,
        Why::TimedOut => BoardError::TimedOut,
        Why::Held => BoardError::Failed("the board's helper is being held until its process is confirmed stopped; see /api/work".into()),
        Why::ShuttingDown => BoardError::Failed("the terminal is shutting down".into()),
        Why::MayHaveRun => BoardError::Failed("the board's helper ended without an answer after the request was sent; it is not repeated because it may already have run".into()),
        Why::Unconfirmed => BoardError::Failed("the board's helper failed and its process could not be confirmed stopped; see /api/work".into()),
    }
}

pub struct FlowBoard {
    /// Program and leading arguments; `--store PATH` follows.
    module: Vec<String>,
    store: PathBuf,
    helpers: Option<Arc<crate::helper::Helpers>>,
    admission: Arc<Admission>,
    limit: Duration,
    kept: std::sync::Mutex<Option<(Instant, Option<SystemTime>, Value)>>,
}

impl FlowBoard {
    pub fn new(module: Vec<String>, store: PathBuf) -> Self {
        FlowBoard { module, store, helpers: None, admission: Arc::new(Admission::new("flow_board", 1, 4)), limit: LIMIT, kept: std::sync::Mutex::new(None) }
    }

    pub fn standard(python: &str, store: PathBuf) -> Self {
        Self::new(vec![python.to_string(), "-m".into(), "athena.flow.board".into()], store)
    }

    #[cfg(test)]
    pub fn with_limit(mut self, limit: Duration) -> Self {
        self.limit = limit;
        self
    }

    #[cfg(test)]
    pub fn with_admission(mut self, running: usize, waiting: usize) -> Self {
        self.admission = Arc::new(Admission::new("flow_board", running, waiting));
        self
    }

    /// The admission as the status route sees it.
    pub fn admission_view(&self) -> Arc<dyn crate::bounded::PoolView> {
        self.admission.view()
    }

    /// Keep the children held by direct commands in a file, so that a restart knows about them (see `bounded.rs`).
    pub fn with_journal(mut self, path: PathBuf) -> Self {
        self.admission = Arc::new(Admission::with_journal("flow_board", 1, 4, path));
        self
    }

    pub fn with_helpers(mut self, helpers: Arc<crate::helper::Helpers>) -> Self {
        self.helpers = Some(helpers);
        self
    }

    fn args(&self) -> Vec<String> {
        vec!["--store".into(), self.store.to_string_lossy().into_owned()]
    }

    /// When the history last changed: the newer of the file and its write-ahead log (a load that has not yet closed the file has written only the log).
    fn modified(&self) -> Option<SystemTime> {
        let when = |p: &std::path::Path| std::fs::metadata(p).and_then(|m| m.modified()).ok();
        let mut wal = self.store.clone().into_os_string();
        wal.push(".wal");
        when(&self.store).max(when(std::path::Path::new(&wal)))
    }

    /// The module started for the request, given only what is left of the request's deadline.
    async fn run_module(&self, args: &[String], budget: &Budget, permit: Permit) -> Result<Vec<u8>, BoardError> {
        if budget.expired() {
            return Err(BoardError::TimedOut);
        }
        let mut cmd = Command::new(self.module.first().ok_or_else(|| BoardError::Failed("the board is not configured".into()))?);
        cmd.args(&self.module[1..]).args(args);
        // the command has an owner of its own: if the deadline passes it is ended and confirmed gone before its place is free, whatever becomes of this request
        let output = self.admission.run_command(permit, cmd, "flow_board", budget).await.map_err(|e| match e {
            DirectError::Timeout => BoardError::TimedOut,
            DirectError::Spawn => BoardError::Failed("could not start the board".into()),
            DirectError::Shutdown => BoardError::Failed("the terminal is shutting down".into()),
        })?;
        if !output.success {
            return Err(BoardError::Failed("the board failed".into()));
        }
        Ok(output.stdout)
    }

    /// The board: kept, else from a helper, else from the module started for it.
    pub async fn board(&self) -> Result<Value, BoardError> {
        let modified = self.modified();
        if let Some((at, when, doc)) = self.kept.lock().unwrap().as_ref() {
            if at.elapsed() < KEEP_FOR && *when == modified {
                return Ok(doc.clone());
            }
        }
        // one deadline for everything below: the wait for the place, the helper and the module run directly all draw on it
        let budget = Budget::new(self.limit);
        let permit = self.admission.admit("flow_board", &budget).await.map_err(|e| match e {
            AdmitError::Full { .. } => BoardError::Overloaded,
            AdmitError::Expired => BoardError::TimedOut,
            AdmitError::Held => BoardError::Failed("the board's place is held by a command that could not be confirmed stopped; see /api/work".into()),
            AdmitError::Closed => BoardError::Failed("the terminal is shutting down".into()),
        })?;
        let args = self.args();
        let stdout = match &self.helpers {
            Some(h) => match h.run_within(crate::helper::HelperOp::FlowBoard, &args, &budget).await.next(crate::helper::HelperOp::FlowBoard.idempotent()) {
                Next::Output(out) => out,
                // only a helper that is certainly finished with the work leaves room for the module to be run once
                Next::RunCommand => self.run_module(&args, &budget, permit).await?,
                // refused, expired or not confirmed gone: the work may still be going or was never accepted, and running it again beside it is not the answer
                Next::Stop(why) => return Err(stopped(why)),
            },
            None => self.run_module(&args, &budget, permit).await?,
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

    // ---- T6 review (R2): the helper, the module run directly and the wait for the place share one deadline; a refusal or expiry does not start the module ----
    use crate::bounded::Limits;

    fn python() -> Option<String> {
        ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists()).map(String::from)
    }
    fn hanging_helper(py: &str, workers: usize, capacity: usize) -> Arc<crate::helper::Helpers> {
        let code = "import json, sys, time\nprint(json.dumps({\"ready\": True}), flush=True)\nfor line in sys.stdin:\n    time.sleep(60)\n";
        let limits = Limits { workers, capacity, deadline: Duration::from_secs(60), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 };
        Arc::new(crate::helper::Helpers::with_limits(vec![py.to_string(), "-c".into(), code.into()], limits))
    }
    /// A board whose module (a Python stand-in, so the test runs where there is no `sh`) counts its starts in a file, sleeps, and prints a board.
    fn counted(tag: &str, sleep_secs: f64) -> Option<(FlowBoard, PathBuf)> {
        let py = python()?;
        let counter = std::env::temp_dir().join(format!("athena-board-{tag}-{}.count", std::process::id()));
        let _ = std::fs::remove_file(&counter);
        let code = format!("import json, time\nopen(r'{}', 'a').write('x\\n')\ntime.sleep({sleep_secs})\nprint(json.dumps({{\"day\": \"2026-10-23\", \"rows\": [], \"events\": {{}}, \"universe\": 0}}))\n", counter.display());
        let store = std::env::temp_dir().join(format!("athena-board-{tag}-{}.duckdb", std::process::id()));
        std::fs::write(&store, b"x").unwrap();
        Some((FlowBoard::new(vec![py, "-c".into(), code], store), counter))
    }    fn starts(counter: &PathBuf) -> usize {
        std::fs::read_to_string(counter).map(|t| t.lines().count()).unwrap_or(0)
    }

    #[tokio::test]
    async fn a_board_whose_helper_expired_does_not_start_the_module_a_second_time() {
        let Some(py) = python() else { return };
        let Some((board, counter)) = counted("expired", 0.0) else { return };
        let board = board.with_helpers(hanging_helper(&py, 1, 2)).with_limit(Duration::from_millis(900));
        assert_eq!(board.board().await, Err(BoardError::TimedOut));
        tokio::time::sleep(Duration::from_millis(1_000)).await;
        assert_eq!(starts(&counter), 0, "the module was not started beside a helper that may still be building the board");
    }

    #[tokio::test]
    async fn a_board_refused_by_a_full_helper_queue_is_overloaded_and_starts_nothing() {
        let Some(py) = python() else { return };
        let Some((board, counter)) = counted("refused", 0.0) else { return };
        let board = Arc::new(board.with_helpers(hanging_helper(&py, 1, 0)).with_admission(2, 2).with_limit(Duration::from_millis(1_500)));
        let first = tokio::spawn({ let b = board.clone(); async move { b.board().await } });
        tokio::time::sleep(Duration::from_millis(400)).await;
        assert_eq!(board.board().await, Err(BoardError::Overloaded));
        assert_eq!(first.await.unwrap(), Err(BoardError::TimedOut));
        assert_eq!(starts(&counter), 0);
    }

    #[tokio::test]
    async fn the_place_for_the_board_is_bounded_and_the_wait_for_it_counts_against_the_deadline() {
        let Some((board, _counter)) = counted("admission", 1.0) else { return };
        let board = Arc::new(board.with_admission(1, 0).with_limit(Duration::from_millis(5_000)));
        let first = tokio::spawn({ let b = board.clone(); async move { b.board().await } });
        tokio::time::sleep(Duration::from_millis(300)).await;
        let t = std::time::Instant::now();
        assert_eq!(board.board().await, Err(BoardError::Overloaded), "the one place is taken and nobody may wait");
        assert_eq!(t.elapsed() < Duration::from_millis(500), true, "{:?}", t.elapsed());
        assert_eq!(first.await.unwrap().is_ok(), true);
    }

    #[test]
    fn a_stopped_helper_request_is_told_what_stopped_it_and_never_as_a_success() {
        assert_eq!(stopped(Why::Overloaded), BoardError::Overloaded);
        assert_eq!(stopped(Why::TimedOut), BoardError::TimedOut);
        for (why, words) in [(Why::Held, "being held"), (Why::Unconfirmed, "could not be confirmed stopped"), (Why::ShuttingDown, "shutting down"), (Why::MayHaveRun, "may already have run")] {
            let shown = stopped(why);
            assert_eq!(matches!(shown, BoardError::Failed(_)), true, "{why:?} must be a failure, not a success or a timeout: {shown:?}");
            let BoardError::Failed(m) = shown else { continue };
            assert_eq!(m.contains(words), true, "{why:?}: {m}");
        }
    }

    #[tokio::test]
    async fn the_board_module_run_after_a_helper_failure_gets_only_what_is_left_of_the_deadline() {
        let Some(py) = python() else { return };
        let Some((board, counter)) = counted("left", 3.0) else { return };
        let code = "import json, sys, time\nprint(json.dumps({\"ready\": True}), flush=True)\nfor line in sys.stdin:\n    r = json.loads(line)\n    time.sleep(0.6)\n    print(json.dumps({\"id\": r[\"id\"], \"ok\": False, \"error\": \"x\"}), flush=True)\n";
        let limits = Limits { workers: 1, capacity: 2, deadline: Duration::from_secs(60), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 };
        let helper = Arc::new(crate::helper::Helpers::with_limits(vec![py, "-c".into(), code.into()], limits));
        let board = board.with_helpers(helper).with_limit(Duration::from_millis(1_400));
        let t = std::time::Instant::now();
        assert_eq!(board.board().await, Err(BoardError::TimedOut));
        assert_eq!(t.elapsed() < Duration::from_millis(2_000), true, "the helper's 0.6 s and the module's wait are one deadline of 1.4 s, not 0.6 + 1.4 s: {:?}", t.elapsed());
        assert_eq!(starts(&counter) <= 1, true, "a helper that failed leaves the module free to be run once");
    }
}
