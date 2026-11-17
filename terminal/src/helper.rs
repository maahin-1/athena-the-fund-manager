//! Long-running Python helpers (`athena.terminal_helper`) for the Fundamentals page, the valuation and the split-adjusted daily bars. Starting Python for
//! each request cost about 1.2 s of the 1.3 s a page took; a warm helper answers in 0.1 to 0.3 s. Each request carries the same arguments the command took and
//! the answer is what the command printed. A helper that could not start, whose command failed or which gave an unusable answer leaves the caller free to run the command itself
//! (once, inside what is left of its deadline), so a broken helper costs speed and not an answer. A request that was **refused, expired or not confirmed stopped is not run again** by the caller: the work may still be
//! going, or was never accepted (`HelperResult`, `may_fall_back`).
//!
//! Since T6 the requests go through a bounded queue (`bounded.rs`): the caller's `limit` is the whole time the request has, from the moment it is submitted, so it covers the wait for a free
//! helper and the helper's start as well as the answer. A request that finds the queue full gets `HelperResult::Refused` at once (and the caller does not run the command: the page says it is overloaded). A helper that does not answer in time is ended (terminate, wait, kill, wait) before its slot is released; one that cannot be confirmed gone is held
//! until it is, or a person resolves it (`/api/work`).
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde_json::json;

use crate::bounded::{Limits, Outcome, Pool, PoolView, ProcessLauncher, Refusal};

/// Loading the modules takes a second or two; this is far more than that.
const START_LIMIT: Duration = Duration::from_secs(60);
/// How many helpers run side by side: the page and its valuation are asked for together.
pub const COUNT: usize = 2;
/// How many requests may wait for a free helper (the page and its valuation are two; this leaves room for the bars and a second page).
const WAITING: usize = 8;

/// The helpers' shared error log, started afresh when it passes two megabytes so it never grows without end.
fn helper_log() -> Option<std::fs::File> {
    let path = std::path::Path::new(".athena").join("terminal").join("helper.log");
    std::fs::create_dir_all(path.parent()?).ok()?;
    if std::fs::metadata(&path).map(|m| m.len() > 2_000_000).unwrap_or(false) {
        let _ = std::fs::rename(&path, path.with_extension("old"));
    }
    std::fs::OpenOptions::new().create(true).append(true).open(path).ok()
}

/// What a helper writes to its error stream is kept in a log (a crash is no longer blind); an unwritable log is simply not used.
fn helper_stderr() -> Stdio {
    helper_log().map(Stdio::from).unwrap_or_else(Stdio::null)
}

pub struct Helpers {
    pool: Arc<Pool<ProcessLauncher>>,
}

impl Helpers {
    #[cfg(test)]
    pub fn new(command: Vec<String>, count: usize) -> Self {
        Self::named("helpers", command, count, None)
    }

    /// As `new`, with the name the pool is listed under in `/api/work` and, when there is one, the journal file that remembers a held helper across a restart.
    pub fn named(name: &str, command: Vec<String>, count: usize, journal: Option<std::path::PathBuf>) -> Self {
        let limits = Limits { workers: count.max(1), capacity: WAITING, deadline: START_LIMIT, start_limit: START_LIMIT, grace: Duration::from_secs(2), recheck: Duration::from_secs(15), history: 200 };
        let launcher = ProcessLauncher::new(command, helper_stderr);
        let pool = match journal {
            Some(path) => Pool::with_journal(name, launcher, limits, path),
            None => Pool::new(name, launcher, limits),
        };
        Helpers { pool: Arc::new(pool) }
    }

    #[cfg(test)]
    pub fn with_limits(command: Vec<String>, limits: Limits) -> Self {
        Helpers { pool: Arc::new(Pool::new("helpers", ProcessLauncher::new(command, helper_stderr), limits)) }
    }

    pub fn standard(python: &str, journal: Option<std::path::PathBuf>) -> Self {
        Self::named("helpers", vec![python.into(), "-m".into(), "athena.terminal_helper".into()], COUNT, journal)
    }

    /// The pool as the status and resolve routes see it.
    pub fn pool_view(&self) -> Arc<dyn PoolView> {
        self.pool.clone()
    }

    /// End every helper (terminate, wait, kill, wait) and stop their workers; later requests are refused as closed.
    #[cfg_attr(not(test), allow(dead_code))]
    pub async fn shutdown(&self) -> crate::bounded::ShutdownReport {
        self.pool.shutdown().await
    }

    /// Start every helper now, so the first page a person opens does not wait for one.
    pub async fn warm(&self) {
        self.pool.warm().await;
    }

    /// What the command `op` prints for `args`, or why it could not be had (the caller decides from `Next` whether to run the command itself). `limit` is the whole time the request has, wait included.
    #[cfg_attr(not(test), allow(dead_code))]
    pub async fn run(&self, op: HelperOp, args: &[String], limit: Duration) -> HelperResult {
        let ticket = match self.pool.enqueue(op.kind(), json!({"op": op.kind(), "args": args}), Some(limit)) {
            Ok(t) => t,
            Err(refusal) => return HelperResult::Refused(refusal),
        };
        let job = ticket.id;
        classify(ticket.result().await, job)
    }

    /// As `run`, inside the request's own deadline: the instant it is up is handed to the pool as it is, so the helper's wait, start and run end at the same moment the request does (a request whose time is already
    /// up never gets a child).
    pub async fn run_within(&self, op: HelperOp, args: &[String], budget: &crate::bounded::Budget) -> HelperResult {
        let ticket = match self.pool.enqueue_until(op.kind(), json!({"op": op.kind(), "args": args}), budget.deadline()) {
            Ok(t) => t,
            Err(refusal) => return HelperResult::Refused(refusal),
        };
        let job = ticket.id;
        classify(ticket.result().await, job)
    }
}

/// What a job's outcome means to a caller that could run the command itself: `Failed` only when the helper is certainly finished with the work.
pub fn classify(outcome: Outcome, job: u64) -> HelperResult {
    match outcome {
        Outcome::Reply(v) if v["ok"] == true => v["out"].as_str().map(|s| HelperResult::Output(s.as_bytes().to_vec())).unwrap_or(HelperResult::Failed("bad_reply")),
        // a failure of the command itself (the helper is fine; running the command directly reports that its own way)
        Outcome::Reply(_) => HelperResult::Failed("command_failed"),
        Outcome::Failed(reason) => HelperResult::Failed(reason),
        Outcome::Expired(_) => HelperResult::Expired { job },
        Outcome::Cancelled => HelperResult::Cancelled { job },
        Outcome::Unconfirmed(_) => HelperResult::Unconfirmed { job },
    }
}

/// The operations a helper serves. A fixed list, so what the work telemetry says about them is never text a person or a page supplied.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HelperOp {
    Page,
    Valuation,
    ChartBars,
    FlowBoard,
}

impl HelperOp {
    /// Whether running it again after it may already have run does no harm. Every operation here only reads (a page, a valuation, adjusted bars, the board); a new operation must say so on purpose.
    pub fn idempotent(self) -> bool {
        match self {
            HelperOp::Page | HelperOp::Valuation | HelperOp::ChartBars | HelperOp::FlowBoard => true,
        }
    }

    /// The name on the wire and in `/api/work`.
    pub fn kind(self) -> &'static str {
        match self {
            HelperOp::Page => "page",
            HelperOp::Valuation => "valuation",
            HelperOp::ChartBars => "chart_bars",
            HelperOp::FlowBoard => "flow_board",
        }
    }
}

/// What a request to a helper came to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HelperResult {
    Output(Vec<u8>),
    /// A helper did not do the work and nothing of it is left running (it could not start, died, failed, or gave an unusable reply): the caller may run the command itself.
    Failed(&'static str),
    /// Refused at admission: the queue is full, every helper is held, or the pool is closed. Nothing was started.
    Refused(Refusal),
    /// The deadline passed (waiting, starting or running). The helper's child is being ended, so running the command again would duplicate work that may still be going.
    Expired { job: u64 },
    Cancelled { job: u64 },
    /// The helper failed and its child could not be confirmed gone.
    Unconfirmed { job: u64 },
}

/// What a caller does with a helper's result: the one place that decides whether the command may be run itself.
#[derive(Debug, PartialEq, Eq)]
pub enum Next {
    Output(Vec<u8>),
    /// The helper is certainly finished with the work and gave nothing: the command may be run (once, inside what is left of the deadline).
    RunCommand,
    /// Do not run the command: the work was refused, may still be going, or the terminal is ending.
    Stop(Why),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Why {
    /// Refused at once: the queue is full.
    Overloaded,
    /// The deadline passed.
    TimedOut,
    /// Every helper is held until its process is confirmed stopped.
    Held,
    ShuttingDown,
    /// The helper failed and its process could not be confirmed stopped.
    Unconfirmed,
    /// The request was sent and the helper ended without a usable answer: a confirmed exit does not prove the operation never completed, and this operation is not known to be safe to repeat.
    MayHaveRun,
}

impl HelperResult {
    /// Only a helper that is certainly finished with the work leaves room for the command to be run again.
    pub fn next(self, idempotent: bool) -> Next {
        match self {
            HelperResult::Output(out) => Next::Output(out),
            // nothing was sent to a child that never started: running the command cannot repeat anything
            HelperResult::Failed("start_failed") | HelperResult::Failed("not_started") => Next::RunCommand,
            // the request was sent and the helper ended without a usable answer: a confirmed exit does not prove the operation never completed, so only an operation known to be safe to repeat is run again
            HelperResult::Failed(_) if idempotent => Next::RunCommand,
            HelperResult::Failed(_) => Next::Stop(Why::MayHaveRun),
            HelperResult::Refused(Refusal::QueueFull { .. }) => Next::Stop(Why::Overloaded),
            HelperResult::Refused(Refusal::Held { .. }) | HelperResult::Refused(Refusal::Unrecovered) => Next::Stop(Why::Held),
            HelperResult::Refused(Refusal::Closed) | HelperResult::Cancelled { .. } => Next::Stop(Why::ShuttingDown),
            HelperResult::Expired { .. } => Next::Stop(Why::TimedOut),
            HelperResult::Unconfirmed { .. } => Next::Stop(Why::Unconfirmed),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn out(r: HelperResult) -> Option<Vec<u8>> {
        if let HelperResult::Output(o) = r { Some(o) } else { None }
    }

    /// A stand-in helper in Python: answers "page" with its arguments joined, fails "valuation", and never answers "chart_bars".
    fn script() -> Option<Helpers> {
        let python = ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists())?;
        let code = r#"
import json, sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    if r["op"] == "page":
        print(json.dumps({"id": r["id"], "ok": True, "out": " ".join(r["args"])}), flush=True)
    elif r["op"] == "valuation":
        print(json.dumps({"id": r["id"], "ok": False, "error": "RuntimeError"}), flush=True)
"#;
        Some(Helpers::new(vec![python.into(), "-c".into(), code.into()], 2))
    }

    #[tokio::test]
    async fn a_helper_answers_with_what_the_command_prints_and_a_failure_or_silence_is_none() {
        let Some(h) = script() else { return };
        h.warm().await;
        let args = vec!["TCS".to_string(), "--price".into(), "3200".into()];
        assert_eq!(out(h.run(HelperOp::Page, &args, Duration::from_secs(10)).await).as_deref(), Some(&b"TCS --price 3200"[..]));
        assert_eq!(out(h.run(HelperOp::Valuation, &args, Duration::from_secs(10)).await), None);
        assert_eq!(out(h.run(HelperOp::ChartBars, &args, Duration::from_millis(300)).await), None);
        // the silent one was dropped and its slot starts afresh
        assert_eq!(out(h.run(HelperOp::Page, &["X".to_string()], Duration::from_secs(10)).await).as_deref(), Some(&b"X"[..]));
    }

    #[tokio::test]
    async fn a_helper_that_cannot_start_is_none() {
        let h = Helpers::new(vec!["no-such-program-xyz".into()], 1);
        assert_eq!(out(h.run(HelperOp::Page, &[], Duration::from_secs(1)).await), None);
    }

    // ---- T6: the caller's limit covers the wait for a helper and the helper's start; the queue is bounded (real Python children, short real limits) ----
    use crate::bounded::Limits;
    use std::sync::Arc;
    use std::time::Instant;

    fn slow_helpers(capacity: usize) -> Option<Arc<Helpers>> {
        let python = ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists())?;
        let code = r#"
import json, sys, time
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    time.sleep(1.2)
    print(json.dumps({"id": r["id"], "ok": True, "out": "done"}), flush=True)
"#;
        let limits = Limits { workers: 1, capacity, deadline: Duration::from_secs(60), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 };
        Some(Arc::new(Helpers::with_limits(vec![python.into(), "-c".into(), code.into()], limits)))
    }

    #[tokio::test]
    async fn a_call_past_the_queue_capacity_gets_none_at_once_and_the_accepted_calls_are_answered() {
        let Some(h) = slow_helpers(1) else { return };
        let a = tokio::spawn({ let h = h.clone(); async move { out(h.run(HelperOp::Page, &[], Duration::from_secs(30)).await) } });
        let b = tokio::spawn({ let h = h.clone(); async move { out(h.run(HelperOp::Page, &[], Duration::from_secs(30)).await) } });
        tokio::time::sleep(Duration::from_millis(100)).await;
        let t = Instant::now();
        assert_eq!(out(h.run(HelperOp::Page, &[], Duration::from_secs(30)).await), None, "no place: the caller runs the command itself");
        assert!(t.elapsed() < Duration::from_millis(500), "{:?}", t.elapsed());
        assert_eq!(a.await.unwrap().as_deref(), Some(&b"done"[..]));
        assert_eq!(b.await.unwrap().as_deref(), Some(&b"done"[..]));
        assert_eq!(h.pool_view().status()["totals"]["refused_queue_full"], 1);
    }

    #[tokio::test]
    async fn the_callers_limit_covers_the_wait_for_a_free_helper() {
        let Some(h) = slow_helpers(2) else { return };
        let first = tokio::spawn({ let h = h.clone(); async move { out(h.run(HelperOp::Page, &[], Duration::from_secs(30)).await) } });
        tokio::time::sleep(Duration::from_millis(50)).await;
        let t = Instant::now();
        // waits ~1.2 s for the helper, then has ~0.8 s of its 2 s left for a 1.2 s answer
        let second = out(h.run(HelperOp::Page, &[], Duration::from_secs(2)).await);
        assert_eq!(second, None);
        assert!(t.elapsed() > Duration::from_millis(1_400) && t.elapsed() < Duration::from_millis(2_600), "ended near the 2 s limit, not after 1.2 + 2 s: {:?}", t.elapsed());
        assert_eq!(first.await.unwrap().as_deref(), Some(&b"done"[..]));
    }

    // ---- T6 review (R2): typed results. A refusal, an expiry and an unconfirmed failure each have their own answer and none of them leaves room for the command to be run a second time ----

    #[tokio::test]
    async fn a_refused_request_is_a_refusal_and_does_not_invite_the_command_to_be_run_again() {
        let Some(h) = slow_helpers(1) else { return };
        let a = tokio::spawn({ let h = h.clone(); async move { out(h.run(HelperOp::Page, &[], Duration::from_secs(30)).await) } });
        let b = tokio::spawn({ let h = h.clone(); async move { out(h.run(HelperOp::Page, &[], Duration::from_secs(30)).await) } });
        tokio::time::sleep(Duration::from_millis(100)).await;
        let r = h.run(HelperOp::Page, &[], Duration::from_secs(30)).await;
        assert_eq!(r, HelperResult::Refused(Refusal::QueueFull { capacity: 1 }));
        assert_eq!(r.next(true), Next::Stop(Why::Overloaded), "the command is not run");
        assert_eq!(a.await.unwrap().as_deref(), Some(&b"done"[..]));
        assert_eq!(b.await.unwrap().as_deref(), Some(&b"done"[..]));
    }

    #[tokio::test]
    async fn an_expired_request_names_its_job_and_does_not_invite_the_command_to_be_run_again() {
        let Some(h) = slow_helpers(1) else { return };
        let r = h.run(HelperOp::Page, &[], Duration::from_millis(400)).await; // the child needs 1.2 s
        assert_eq!(r, HelperResult::Expired { job: 1 });
        assert_eq!(r.next(true), Next::Stop(Why::TimedOut), "the command is not run");
    }

    #[tokio::test]
    async fn a_command_that_failed_and_a_helper_that_could_not_start_leave_the_command_free_to_run() {
        let Some(h) = script() else { return };
        let r = h.run(HelperOp::Valuation, &["TCS".to_string()], Duration::from_secs(10)).await;
        assert_eq!(r, HelperResult::Failed("command_failed"));
        assert_eq!(r.next(true), Next::RunCommand, "the helper finished with the work: running the command reports its failure its own way");
        let none = Helpers::new(vec!["no-such-program-xyz".into()], 1);
        let r = none.run(HelperOp::Page, &[], Duration::from_secs(1)).await;
        assert_eq!(r, HelperResult::Failed("start_failed"));
        assert_eq!(r.next(true), Next::RunCommand, "nothing was sent to a child that never started");
    }

    #[test]
    fn every_outcome_has_a_typed_meaning_and_only_a_finished_helper_allows_a_second_run() {
        use serde_json::json;
        assert_eq!(classify(Outcome::Expired("deadline_exceeded"), 4), HelperResult::Expired { job: 4 });
        assert_eq!(classify(Outcome::Cancelled, 5), HelperResult::Cancelled { job: 5 });
        assert_eq!(classify(Outcome::Unconfirmed("no_answer"), 6), HelperResult::Unconfirmed { job: 6 });
        assert_eq!(classify(Outcome::Failed("start_failed"), 7), HelperResult::Failed("start_failed"));
        assert_eq!(classify(Outcome::Failed("no_answer"), 8), HelperResult::Failed("no_answer"));
        assert_eq!(classify(Outcome::Reply(json!({"ok": true})), 9), HelperResult::Failed("bad_reply"));
        assert_eq!(classify(Outcome::Reply(json!({"ok": false, "error": "x"})), 1), HelperResult::Failed("command_failed"));
        assert_eq!(classify(Outcome::Reply(json!({"ok": true, "out": "x"})), 1), HelperResult::Output(b"x".to_vec()));
        let cases = [
            (HelperResult::Expired { job: 1 }, Next::Stop(Why::TimedOut)),
            (HelperResult::Cancelled { job: 1 }, Next::Stop(Why::ShuttingDown)),
            (HelperResult::Unconfirmed { job: 1 }, Next::Stop(Why::Unconfirmed)),
            (HelperResult::Refused(Refusal::QueueFull { capacity: 1 }), Next::Stop(Why::Overloaded)),
            (HelperResult::Refused(Refusal::Held { job: 1 }), Next::Stop(Why::Held)),
            (HelperResult::Refused(Refusal::Closed), Next::Stop(Why::ShuttingDown)),
            (HelperResult::Failed("x"), Next::RunCommand),
            (HelperResult::Output(b"y".to_vec()), Next::Output(b"y".to_vec())),
        ];
        for (result, next) in cases {
            let shown = format!("{result:?}");
            assert_eq!(result.next(true), next, "{shown}");
        }
    }

    #[tokio::test]
    async fn after_shutdown_the_helpers_are_ended_and_a_request_is_refused_as_closed() {
        let Some(h) = slow_helpers(1) else { return };
        h.warm().await;
        let report = h.shutdown().await;
        assert_eq!(report.ended, 1, "the warm child was ended and confirmed gone");
        assert_eq!(h.run(HelperOp::Page, &[], Duration::from_secs(5)).await, HelperResult::Refused(Refusal::Closed));
    }

    // ---- T6 review 2: a confirmed exit does not prove the operation never completed ----
    #[test]
    fn a_failure_after_the_request_was_sent_allows_a_second_run_only_for_an_operation_known_to_be_safe_to_repeat() {
        for reason in ["no_answer", "command_failed", "bad_reply"] {
            assert_eq!(HelperResult::Failed(reason).next(true), Next::RunCommand, "{reason}: an idempotent operation may be run again inside its deadline");
            assert_eq!(HelperResult::Failed(reason).next(false), Next::Stop(Why::MayHaveRun), "{reason}: it may already have run");
        }
        assert_eq!(HelperResult::Failed("start_failed").next(false), Next::RunCommand, "nothing was sent to a child that never started");
        assert_eq!(HelperResult::Refused(Refusal::Unrecovered).next(true), Next::Stop(Why::Held));
        for op in [HelperOp::Page, HelperOp::Valuation, HelperOp::ChartBars, HelperOp::FlowBoard] {
            assert_eq!(op.idempotent(), true, "{op:?} only reads");
        }
    }
}
