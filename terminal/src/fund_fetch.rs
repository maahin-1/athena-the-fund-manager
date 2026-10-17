//! Fetching one company's filings from the page: statements, then shareholding, then documents, by running the same three Python commands that are
//! run by hand for the whole market. One company at a time (the exchange is asked slowly and the store has one writer), with a status the page polls.
//! Nothing from a child's output reaches the page; failures become fixed messages.
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::process::Command;

/// A stage's modules in order, with the label the page shows while it runs.
pub const STAGES: [(&str, &str); 3] = [
    ("athena.fundamentals.ingest", "fetching statements"),
    ("athena.fundamentals.holdings", "fetching shareholding"),
    ("athena.fundamentals.documents", "fetching documents"),
];
const STAGE_LIMIT: Duration = Duration::from_secs(600);
/// The exit code the fetch commands use when the exchange refused them (see `runner.run_each`).
const BLOCKED: i32 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Idle,
    Running { symbol: String, stage: usize },
    Done { symbol: String },
    Failed { symbol: String, message: &'static str },
}

#[derive(Debug, PartialEq, Eq)]
pub enum StartError {
    BadSymbol,
    /// Another company is being fetched.
    Busy(String),
}

/// How a stage is run: the module to start and the symbol. Replaced in tests.
pub type Launcher = Arc<dyn Fn(&str, &str) -> Command + Send + Sync>;

pub struct FundFetch {
    state: Mutex<State>,
    launch: Launcher,
}

impl FundFetch {
    pub fn new(launch: Launcher) -> Arc<Self> {
        Arc::new(FundFetch { state: Mutex::new(State::Idle), launch })
    }

    pub fn standard(python: &str, store: PathBuf) -> Arc<Self> {
        let python = python.to_string();
        Self::new(Arc::new(move |module, symbol| {
            let mut cmd = Command::new(&python);
            cmd.args(["-m", module, "--universe", symbol, "--store"]).arg(&store);
            cmd
        }))
    }

    pub fn status(&self) -> Value {
        match &*self.state.lock().unwrap() {
            State::Idle => json!({"state": "idle"}),
            State::Running { symbol, stage } => json!({"state": "running", "symbol": symbol, "stage": STAGES[*stage].1, "step": stage + 1, "of": STAGES.len()}),
            State::Done { symbol } => json!({"state": "done", "symbol": symbol}),
            State::Failed { symbol, message } => json!({"state": "failed", "symbol": symbol, "error": message}),
        }
    }

    /// Start fetching `symbol` unless that is already running (which is fine: the page just follows it) or another company is.
    pub fn start(self: &Arc<Self>, symbol: &str) -> Result<(), StartError> {
        if !crate::fundamentals::symbol_ok(symbol) {
            return Err(StartError::BadSymbol);
        }
        {
            let mut g = self.state.lock().unwrap();
            if let State::Running { symbol: running, .. } = &*g {
                return if running == symbol { Ok(()) } else { Err(StartError::Busy(running.clone())) };
            }
            *g = State::Running { symbol: symbol.to_string(), stage: 0 };
        }
        let me = self.clone();
        let symbol = symbol.to_string();
        tokio::spawn(async move { me.run(symbol).await });
        Ok(())
    }

    async fn run(self: Arc<Self>, symbol: String) {
        for (stage, (module, _)) in STAGES.iter().enumerate() {
            *self.state.lock().unwrap() = State::Running { symbol: symbol.clone(), stage };
            let mut cmd = (self.launch)(module, &symbol);
            cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).kill_on_drop(true);
            let outcome = match cmd.spawn() {
                Err(_) => Err("the fetch could not be started"),
                Ok(mut child) => match tokio::time::timeout(STAGE_LIMIT, child.wait()).await {
                    Err(_) => {
                        let _ = child.kill().await;
                        Err("the fetch took too long and was stopped")
                    }
                    Ok(Ok(status)) if status.success() => Ok(()),
                    Ok(Ok(status)) if status.code() == Some(BLOCKED) => Err("the exchange refused the requests; try again in a few minutes"),
                    Ok(_) => Err("the fetch failed"),
                },
            };
            if let Err(message) = outcome {
                *self.state.lock().unwrap() = State::Failed { symbol, message };
                return;
            }
        }
        *self.state.lock().unwrap() = State::Done { symbol };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn exit_with(code: i32) -> Launcher {
        Arc::new(move |_module, _symbol| {
            if cfg!(windows) {
                let mut c = Command::new("cmd");
                c.args(["/C", &format!("exit {code}")]);
                c
            } else {
                let mut c = Command::new("sh");
                c.args(["-c", &format!("exit {code}")]);
                c
            }
        })
    }

    async fn until_settled(f: &Arc<FundFetch>) -> Value {
        for _ in 0..200 {
            let s = f.status();
            if s["state"] != "running" {
                return s;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        f.status()
    }

    #[test]
    fn symbols_are_checked_before_anything_runs() {
        let f = FundFetch::new(exit_with(0));
        assert_eq!(f.start("rv nl"), Err(StartError::BadSymbol));
        assert_eq!(f.start(""), Err(StartError::BadSymbol));
        assert_eq!(f.status()["state"], "idle");
    }

    #[tokio::test]
    async fn the_three_stages_run_in_order_and_the_job_ends_done() {
        let seen = Arc::new(Mutex::new(Vec::<String>::new()));
        let log = seen.clone();
        let f = FundFetch::new(Arc::new(move |module, symbol| {
            log.lock().unwrap().push(format!("{module} {symbol}"));
            exit_with(0)(module, symbol)
        }));
        f.start("RVNL").unwrap();
        assert_eq!(f.status()["state"], "running");
        assert_eq!(until_settled(&f).await, json!({"state": "done", "symbol": "RVNL"}));
        assert_eq!(*seen.lock().unwrap(), ["athena.fundamentals.ingest RVNL", "athena.fundamentals.holdings RVNL", "athena.fundamentals.documents RVNL"]);
    }

    #[tokio::test]
    async fn a_failing_stage_stops_the_job_with_a_fixed_message() {
        let f = FundFetch::new(exit_with(1));
        f.start("RVNL").unwrap();
        assert_eq!(until_settled(&f).await, json!({"state": "failed", "symbol": "RVNL", "error": "the fetch failed"}));
        let blocked = FundFetch::new(exit_with(3));
        blocked.start("RVNL").unwrap();
        assert!(until_settled(&blocked).await["error"].as_str().unwrap().contains("refused"));
    }

    #[tokio::test]
    async fn a_program_that_cannot_start_is_a_failure_not_a_crash() {
        let f = FundFetch::new(Arc::new(|_m, _s| Command::new("no-such-program-xyz")));
        f.start("RVNL").unwrap();
        assert_eq!(until_settled(&f).await["error"], "the fetch could not be started");
    }

    #[tokio::test]
    async fn the_same_company_may_be_asked_twice_but_another_waits() {
        let slow: Launcher = Arc::new(|_m, _s| {
            if cfg!(windows) {
                let mut c = Command::new("ping");
                c.args(["-n", "3", "127.0.0.1"]);
                c
            } else {
                let mut c = Command::new("sleep");
                c.arg("2");
                c
            }
        });
        let f = FundFetch::new(slow);
        f.start("RVNL").unwrap();
        assert_eq!(f.start("RVNL"), Ok(()));
        assert_eq!(f.start("TCS"), Err(StartError::Busy("RVNL".into())));
    }
}
