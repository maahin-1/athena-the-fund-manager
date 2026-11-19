//! The live analysis: the project's own Python dashboard service, kept running as one worker process (`athena.dashboard.worker`).
//! It is started when the terminal starts (building the service takes about 15 seconds), or on the first request if that failed, and then answers one request at a time over
//! JSON lines.
//!
//! Since T6 the requests go through a bounded queue (`bounded.rs`): one worker (the conservative count the terminal always had), four places for waiting requests, and one deadline per request
//! that covers its wait in the queue, the worker's start and the answer (300 s in all, 180 s of it at most for a start). A request past the queue's capacity is refused at once (`Busy`); a request whose
//! deadline passes ends with `TimedOut(job)`; a worker that did not answer, or whose request was cancelled, is ended (terminate, wait, kill, wait) and released only when its exit is confirmed. A worker that
//! cannot be confirmed gone is held (`Held` for new requests) until its exit is confirmed later or a person resolves it (`/api/work`).
use std::process::Stdio;
use std::sync::Arc;

use serde_json::{json, Value};

use crate::bounded::{Limits, Outcome, Pool, PoolView, ProcessLauncher, Refusal};

const MAX_QUERY: usize = 60;
const MAX_ERROR: usize = 240;

#[derive(Debug, PartialEq, Eq)]
pub enum AnalyseError {
    /// The query is empty, too long or has characters no instrument name has.
    BadQuery,
    /// The worker could not start, died, or did not answer.
    Unavailable,
    /// The worker answered with a failure; the text is the project's own message.
    Failed(String),
    /// The queue is full: refused at once, nothing was queued (T6).
    Busy,
    /// The job's deadline (queue wait plus execution) passed; the number is the job's ID (T6).
    TimedOut(u64),
    /// A worker that could not be confirmed gone is held, and no new job may take it until it is resolved (T6).
    Held,
    /// The request failed and its worker could not be confirmed gone (the number is the job's ID): whatever it was doing may still be going.
    Unconfirmed(u64),
    /// The service is shutting down.
    Closed,
}

/// The most a strategy tree may weigh as JSON (the strategy model's own limits keep a valid one far below this).
pub const MAX_STRATEGY_BYTES: usize = 20_000;

pub struct AnalyseWorker {
    pool: Arc<Pool<ProcessLauncher>>,
}

/// Characters in a ticker, an ISIN or a company name.
pub fn query_ok(query: &str) -> bool {
    let q = query.trim();
    !q.is_empty() && q.chars().count() <= MAX_QUERY && q.chars().all(|c| c.is_alphanumeric() || " &.,-^_'()".contains(c))
}

impl AnalyseWorker {
    pub fn new(command: Vec<String>) -> Self {
        Self::with_limits(command, Limits::default())
    }

    /// The project's worker; with a journal file, a worker that could not be confirmed gone is remembered across a restart.
    pub fn standard(python: &str, journal: Option<std::path::PathBuf>) -> Self {
        let command = vec![python.into(), "-m".into(), "athena.dashboard.worker".into()];
        match journal {
            Some(path) => AnalyseWorker { pool: Arc::new(Pool::with_journal("analyse", ProcessLauncher::new(command, Stdio::null), Limits::default(), path)) },
            None => Self::new(command),
        }
    }

    pub fn with_limits(command: Vec<String>, limits: Limits) -> Self {
        AnalyseWorker { pool: Arc::new(Pool::new("analyse", ProcessLauncher::new(command, Stdio::null), limits)) }
    }

    /// The pool as the status and resolve routes see it.
    pub fn pool_view(&self) -> Arc<dyn PoolView> {
        self.pool.clone()
    }

    /// Start the worker now (about 15 seconds) so the first analysis asked for does not wait for it. It goes through the queue, so a request that comes meanwhile waits its turn on the same deadline.
    pub async fn warm(&self) {
        self.pool.warm().await;
    }

    /// The dashboard view for a typed ticker, ISIN or name, as the worker built it (`relatives` lists related instruments).
    pub async fn view(&self, query: &str, relatives: bool) -> Result<Value, AnalyseError> {
        if !query_ok(query) {
            return Err(AnalyseError::BadQuery);
        }
        self.call("view", json!({"op": "view", "query": query.trim(), "relatives": relatives})).await
    }

    /// The built-in rules replayed over the symbol's history, each against buy and hold, with the numbers of each curve; or, with a `strategy` (the tree
    /// the formula language writes), that strategy alone. The worker validates the tree again, so what is checked here is only its size and that it is an object.
    pub async fn backtest(&self, symbol: &str, strategy: Option<Value>) -> Result<Value, AnalyseError> {
        if !query_ok(symbol) {
            return Err(AnalyseError::BadQuery);
        }
        let mut payload = json!({"op": "backtest", "symbol": symbol.trim()});
        if let Some(tree) = strategy {
            if !tree.is_object() || tree.to_string().len() > MAX_STRATEGY_BYTES {
                return Err(AnalyseError::BadQuery);
            }
            payload["strategy"] = tree;
        }
        self.call("backtest", payload).await
    }

    async fn call(&self, kind: &'static str, payload: Value) -> Result<Value, AnalyseError> {
        let ticket = match self.pool.enqueue(kind, payload, None) {
            Ok(t) => t,
            Err(Refusal::QueueFull { .. }) => return Err(AnalyseError::Busy),
            Err(Refusal::Held { .. }) => return Err(AnalyseError::Held),
            Err(Refusal::Closed) => return Err(AnalyseError::Closed),
            Err(Refusal::Unrecovered) => return Err(AnalyseError::Held),
        };
        let id = ticket.id;
        match ticket.result().await {
            Outcome::Reply(reply) if reply["ok"] == true => Ok(reply["result"].clone()),
            Outcome::Reply(reply) => {
                let text = reply["error"].as_str().unwrap_or("the analysis failed");
                Err(AnalyseError::Failed(text.chars().take(MAX_ERROR).collect()))
            }
            Outcome::Expired(_) => Err(AnalyseError::TimedOut(id)),
            Outcome::Unconfirmed(_) => Err(AnalyseError::Unconfirmed(id)),
            Outcome::Failed(_) | Outcome::Cancelled => Err(AnalyseError::Unavailable),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queries_are_names_tickers_and_isins_only() {
        for good in ["TCS", "M&M", "INE467B01029", "Tata Consultancy Services", "BAJAJ-AUTO", "Nifty 50"] {
            assert!(query_ok(good), "{good}");
        }
        for bad in ["", "   ", "a;b", "x\n y", "$(rm)", "a/b", &"x".repeat(61)] {
            assert!(!query_ok(bad), "{bad}");
        }
    }

    // a stand-in worker written in Python so the protocol is exercised end to end; skipped where the project's Python is not at hand
    fn fake(script: &str) -> Option<AnalyseWorker> {
        let python = ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists())?;
        Some(AnalyseWorker::new(vec![python.into(), "-c".into(), script.into()]))
    }

    const ECHO: &str = r#"
import json, sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    if r["query"] == "BOOM":
        print(json.dumps({"id": r["id"], "ok": False, "error": "no such instrument"}), flush=True)
    elif r["query"] == "DIE":
        sys.exit(0)
    else:
        print("noise that is not json", flush=True)
        print(json.dumps({"id": 99999, "ok": True, "result": "someone else's"}), flush=True)
        print(json.dumps({"id": r["id"], "ok": True, "result": {"query": r["query"], "relatives": r["relatives"]}}), flush=True)
"#;

    #[tokio::test]
    async fn a_request_gets_its_own_answer_and_a_failure_keeps_the_worker() {
        let Some(w) = fake(ECHO) else { return };
        assert_eq!(w.view("TCS", true).await.unwrap(), json!({"query": "TCS", "relatives": true}));
        assert_eq!(w.view("BOOM", false).await, Err(AnalyseError::Failed("no such instrument".into())));
        assert_eq!(w.view("INFY", false).await.unwrap()["query"], "INFY"); // the same process went on
    }

    #[tokio::test]
    async fn a_backtest_goes_through_the_same_worker() {
        let script = r#"
import json, sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    print(json.dumps({"id": r["id"], "ok": True, "result": {"op": r["op"], "symbol": r.get("symbol")}}), flush=True)
"#;
        let Some(w) = fake(script) else { return };
        assert_eq!(w.backtest("SBIN", None).await.unwrap(), json!({"op": "backtest", "symbol": "SBIN"}));
        assert_eq!(w.view("TCS", false).await.unwrap()["op"], "view");
        assert_eq!(w.backtest("a;b", None).await, Err(AnalyseError::BadQuery));
    }

    #[tokio::test]
    async fn a_strategy_is_passed_on_whole_and_a_bad_or_huge_one_is_refused_before_the_worker_hears_of_it() {
        let script = r#"
import json, sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    print(json.dumps({"id": r["id"], "ok": True, "result": {"strategy": r.get("strategy")}}), flush=True)
"#;
        let Some(w) = fake(script) else { return };
        let tree = json!({"version": 1, "name": "x", "entry": {"op": "gt", "left": {"price": "close"}, "right": {"const": 1}}});
        assert_eq!(w.backtest("SBIN", Some(tree.clone())).await.unwrap(), json!({"strategy": tree}));
        assert_eq!(w.backtest("SBIN", Some(json!("close > 1"))).await, Err(AnalyseError::BadQuery)); // text is not a tree
        assert_eq!(w.backtest("SBIN", Some(json!([1]))).await, Err(AnalyseError::BadQuery));
        let huge = json!({"name": "x".repeat(MAX_STRATEGY_BYTES)});
        assert_eq!(w.backtest("SBIN", Some(huge)).await, Err(AnalyseError::BadQuery));
    }

    #[tokio::test]
    async fn a_dead_worker_is_reported_and_replaced_on_the_next_request() {
        let Some(w) = fake(ECHO) else { return };
        assert!(w.view("TCS", false).await.is_ok());
        assert_eq!(w.view("DIE", false).await, Err(AnalyseError::Unavailable));
        assert!(w.view("TCS", false).await.is_ok()); // a fresh process
    }

    #[tokio::test]
    async fn a_missing_program_and_a_bad_query_are_refused_without_hanging() {
        assert_eq!(AnalyseWorker::new(vec!["no-such-program-xyz".into()]).view("TCS", false).await, Err(AnalyseError::Unavailable));
        assert_eq!(AnalyseWorker::new(vec![]).view("a;b", false).await, Err(AnalyseError::BadQuery));
    }

    // ---- T6: bounded queue, deadlines that include the wait and the start, and children ended before their worker is released (real Python children, short real limits) ----
    use crate::bounded::Limits;
    use std::time::{Duration, Instant};

    /// Queries starting with `S` take 1.2 s, `HANG` takes a minute (and ignores its input closing), anything else answers at once.
    const SLOWISH: &str = r#"
import json, sys, time, os
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    r = json.loads(line)
    q = r["query"]
    time.sleep(60 if q == "HANG" else 1.2 if q.startswith("S") else 0)
    print(json.dumps({"id": r["id"], "ok": True, "result": {"pid": os.getpid(), "query": q}}), flush=True)
"#;

    fn limits(capacity: usize, deadline_ms: u64) -> Limits {
        Limits { workers: 1, capacity, deadline: Duration::from_millis(deadline_ms), start_limit: Duration::from_secs(20), grace: Duration::from_millis(300), recheck: Duration::from_secs(5), history: 50 }
    }
    fn slowish(l: Limits) -> Option<Arc<AnalyseWorker>> {
        let python = ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists())?;
        Some(Arc::new(AnalyseWorker::with_limits(vec![python.into(), "-c".into(), SLOWISH.into()], l)))
    }

    #[tokio::test]
    async fn a_full_queue_is_refused_at_once_and_the_accepted_requests_still_finish() {
        let Some(w) = slowish(limits(1, 60_000)) else { return };
        let (a, b) = (tokio::spawn({ let w = w.clone(); async move { w.view("S1", false).await } }), tokio::spawn({ let w = w.clone(); async move { w.view("S2", false).await } }));
        tokio::time::sleep(Duration::from_millis(100)).await; // both are in: one runs, one waits
        let t = Instant::now();
        assert_eq!(w.view("S3", false).await, Err(AnalyseError::Busy));
        assert!(t.elapsed() < Duration::from_millis(500), "refused without waiting: {:?}", t.elapsed());
        assert!(a.await.unwrap().is_ok() && b.await.unwrap().is_ok(), "the running and the queued request were not disturbed");
    }

    #[tokio::test]
    async fn the_deadline_includes_the_wait_so_a_second_request_behind_a_long_one_times_out_on_the_same_clock() {
        let Some(w) = slowish(limits(2, 2_000)) else { return };
        // (warmed first, so the first request's 2 s is its 1.2 s answer and not also a Python start: on a busy machine a start of a second or more made this test fail now and then)
        w.warm().await;
        let first = tokio::spawn({ let w = w.clone(); async move { w.view("S1", false).await } });
        tokio::time::sleep(Duration::from_millis(50)).await;
        let t = Instant::now();
        let second = w.view("S2", false).await; // waits ~1.2 s, then needs 1.2 s with ~0.75 s of its 2 s left
        assert!(matches!(second, Err(AnalyseError::TimedOut(_))), "{second:?}");
        assert!(t.elapsed() > Duration::from_millis(1_400) && t.elapsed() < Duration::from_millis(2_600), "it ended near its own 2 s deadline, not 1.2 + 1.2 s: {:?}", t.elapsed());
        assert!(first.await.unwrap().is_ok());
    }

    #[tokio::test]
    async fn a_request_past_its_deadline_has_its_child_ended_and_confirmed_before_the_next_gets_a_worker() {
        // (2.5 s, and warmed first: with a deadline near a Python start's time, a slow start on a busy machine ended the first request in its start, or the next one before its fresh child answered)
        let Some(w) = slowish(limits(2, 2_500)) else { return };
        w.warm().await;
        // (the test's own bound, so a worker that ignores its deadline fails this test instead of hanging it)
        let hung = tokio::time::timeout(Duration::from_secs(10), w.view("HANG", false)).await.expect("the request ended near its deadline");
        let Err(AnalyseError::TimedOut(id)) = hung else { panic!("{hung:?}") };
        let ok = w.view("OK", false).await.expect("a fresh child answers");
        assert!(ok["pid"].is_u64());
        let status = w.pool_view().status();
        let job = status["recent"].as_array().unwrap().iter().find(|r| r["id"] == id).cloned().unwrap();
        assert_eq!((job["state"].as_str(), job["reason"].as_str()), (Some("expired"), Some("deadline_exceeded")), "{status}");
        assert_eq!(status["retained"].as_array().unwrap().len(), 0, "the child was confirmed gone, so nothing is held");
    }
}
