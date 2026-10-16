//! The live analysis: the project's own Python dashboard service, kept running as one worker process (`athena.dashboard.worker`).
//! It is started on the first request (building the service takes about 15 seconds) and then answers one request at a time over
//! JSON lines. A request that fails, times out or finds the worker gone drops the process, so the next request starts a fresh one.
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;

const START_LIMIT: Duration = Duration::from_secs(180);
const ANSWER_LIMIT: Duration = Duration::from_secs(300);
const MAX_QUERY: usize = 60;
const MAX_ERROR: usize = 240;

#[derive(Debug, PartialEq, Eq)]
pub enum AnalyseError {
    /// The query is empty, too long or has characters no instrument name has.
    BadQuery,
    /// The worker could not start, died, or did not answer in time.
    Unavailable,
    /// The worker answered with a failure; the text is the project's own message.
    Failed(String),
}

struct Live {
    _child: Child, // kill_on_drop: dropping this ends the process
    stdin: ChildStdin,
    out: Lines<BufReader<ChildStdout>>,
}

pub struct AnalyseWorker {
    command: Vec<String>,
    live: Mutex<Option<Live>>,
    next_id: AtomicU64,
}

/// Characters in a ticker, an ISIN or a company name.
pub fn query_ok(query: &str) -> bool {
    let q = query.trim();
    !q.is_empty() && q.chars().count() <= MAX_QUERY && q.chars().all(|c| c.is_alphanumeric() || " &.,-^_'()".contains(c))
}

impl AnalyseWorker {
    pub fn new(command: Vec<String>) -> Self {
        AnalyseWorker { command, live: Mutex::new(None), next_id: AtomicU64::new(1) }
    }

    pub fn standard(python: &str) -> Self {
        Self::new(vec![python.into(), "-m".into(), "athena.dashboard.worker".into()])
    }

    async fn start(&self) -> Option<Live> {
        let mut cmd = Command::new(self.command.first()?);
        cmd.args(&self.command[1..]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        let mut child = cmd.spawn().ok()?;
        let stdin = child.stdin.take()?;
        let mut out = BufReader::new(child.stdout.take()?).lines();
        let ready = tokio::time::timeout(START_LIMIT, async {
            while let Ok(Some(line)) = out.next_line().await {
                if serde_json::from_str::<Value>(&line).ok().is_some_and(|v| v["ready"] == true) {
                    return true;
                }
            }
            false
        })
        .await
        .unwrap_or(false);
        ready.then_some(Live { _child: child, stdin, out })
    }

    /// The dashboard view for a typed ticker, ISIN or name, as the worker built it (`relatives` lists related instruments).
    pub async fn view(&self, query: &str, relatives: bool) -> Result<Value, AnalyseError> {
        if !query_ok(query) {
            return Err(AnalyseError::BadQuery);
        }
        self.call(json!({"op": "view", "query": query.trim(), "relatives": relatives})).await
    }

    /// The built-in rules replayed over the symbol's history, each against buy and hold, with the numbers of each curve.
    pub async fn backtest(&self, symbol: &str) -> Result<Value, AnalyseError> {
        if !query_ok(symbol) {
            return Err(AnalyseError::BadQuery);
        }
        self.call(json!({"op": "backtest", "symbol": symbol.trim()})).await
    }

    async fn call(&self, mut payload: Value) -> Result<Value, AnalyseError> {
        let mut guard = self.live.lock().await; // one request at a time: the worker is one process and the model calls are slow
        if guard.is_none() {
            *guard = Some(self.start().await.ok_or(AnalyseError::Unavailable)?);
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        payload["id"] = json!(id);
        let request = payload.to_string() + "
";
        let outcome = {
            let live = guard.as_mut().expect("started above");
            tokio::time::timeout(ANSWER_LIMIT, async {
                live.stdin.write_all(request.as_bytes()).await.ok()?;
                live.stdin.flush().await.ok()?;
                while let Ok(Some(line)) = live.out.next_line().await {
                    if let Ok(v) = serde_json::from_str::<Value>(&line) {
                        if v["id"] == id {
                            return Some(v);
                        }
                    }
                }
                None // the worker closed its output
            })
            .await
            .ok()
            .flatten()
        };
        let Some(reply) = outcome else {
            *guard = None; // dead, stuck or silent: the next request starts a new one
            return Err(AnalyseError::Unavailable);
        };
        if reply["ok"] == true {
            Ok(reply["result"].clone())
        } else {
            let text = reply["error"].as_str().unwrap_or("the analysis failed");
            Err(AnalyseError::Failed(text.chars().take(MAX_ERROR).collect()))
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
        assert_eq!(w.backtest("SBIN").await.unwrap(), json!({"op": "backtest", "symbol": "SBIN"}));
        assert_eq!(w.view("TCS", false).await.unwrap()["op"], "view");
        assert_eq!(w.backtest("a;b").await, Err(AnalyseError::BadQuery));
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
}
