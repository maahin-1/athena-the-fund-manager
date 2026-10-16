//! "Make a fresh verdict": runs the project's own Python command for one symbol, one at a time, and reports only a fixed state.
//! The command calls the language model (about 30 seconds); nothing from its output is forwarded to the page.
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{json, Value};

const LIMIT: Duration = Duration::from_secs(300);

#[derive(Debug, Clone, PartialEq, Eq)]
enum State {
    Idle,
    Running(String),
    Done(String),
    Failed(String, String),
}

#[derive(Debug, PartialEq, Eq)]
pub enum StartError {
    BadSymbol,
    Busy,
}

pub fn symbol_ok(symbol: &str) -> bool {
    !symbol.is_empty() && symbol.len() <= 20 && symbol.chars().all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '&' || c == '-')
}

#[derive(Clone)]
pub struct VerdictJobs {
    /// Program and its arguments; the symbol is added last.
    command: Arc<Vec<String>>,
    state: Arc<Mutex<State>>,
}

impl VerdictJobs {
    pub fn new(command: Vec<String>) -> Self {
        VerdictJobs { command: Arc::new(command), state: Arc::new(Mutex::new(State::Idle)) }
    }

    pub fn standard(python: &str) -> Self {
        Self::new(vec![python.into(), "-m".into(), "athena.verdicts.cli".into(), "--symbol".into()])
    }

    pub fn start(&self, symbol: &str) -> Result<(), StartError> {
        if !symbol_ok(symbol) {
            return Err(StartError::BadSymbol);
        }
        {
            let mut s = self.state.lock().unwrap();
            if matches!(*s, State::Running(_)) {
                return Err(StartError::Busy);
            }
            *s = State::Running(symbol.to_string());
        }
        let (command, state, symbol) = (self.command.clone(), self.state.clone(), symbol.to_string());
        tokio::spawn(async move {
            let mut cmd = tokio::process::Command::new(&command[0]);
            cmd.args(&command[1..]).arg(&symbol).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).kill_on_drop(true);
            let outcome = match cmd.spawn() {
                Err(_) => State::Failed(symbol.clone(), "could not start the verdict command".into()),
                Ok(mut child) => match tokio::time::timeout(LIMIT, child.wait()).await {
                    Err(_) => {
                        let _ = child.kill().await;
                        State::Failed(symbol.clone(), "took longer than five minutes and was stopped".into())
                    }
                    Ok(Ok(status)) if status.success() => State::Done(symbol.clone()),
                    Ok(Ok(status)) => State::Failed(symbol.clone(), format!("the verdict command failed (exit code {})", status.code().unwrap_or(-1))),
                    Ok(Err(_)) => State::Failed(symbol.clone(), "the verdict command could not be waited on".into()),
                },
            };
            *state.lock().unwrap() = outcome;
        });
        Ok(())
    }

    pub fn status(&self) -> Value {
        match &*self.state.lock().unwrap() {
            State::Idle => json!({"state": "idle"}),
            State::Running(s) => json!({"state": "running", "symbol": s}),
            State::Done(s) => json!({"state": "done", "symbol": s}),
            State::Failed(s, why) => json!({"state": "failed", "symbol": s, "message": why}),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn finished(jobs: &VerdictJobs) -> Value {
        for _ in 0..100 {
            let s = jobs.status();
            if s["state"] != "running" {
                return s;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        panic!("the job never finished");
    }

    #[test]
    fn symbols_are_checked() {
        assert!(symbol_ok("SBIN") && symbol_ok("M&M") && symbol_ok("BAJAJ-AUTO"));
        for bad in ["", "sbin", "A B", "A;B", "../x", &"X".repeat(21)] {
            assert!(!symbol_ok(bad), "{bad}");
        }
    }

    #[tokio::test]
    async fn a_good_command_ends_done_and_only_one_runs_at_a_time() {
        let jobs = VerdictJobs::new(vec!["cmd".into(), "/C".into(), "ping -n 2 127.0.0.1 >NUL & exit 0 &".into()]);
        assert_eq!(jobs.status()["state"], "idle");
        jobs.start("SBIN").unwrap();
        assert_eq!(jobs.status(), json!({"state": "running", "symbol": "SBIN"}));
        assert_eq!(jobs.start("TCS"), Err(StartError::Busy));
        assert_eq!(finished(&jobs).await, json!({"state": "done", "symbol": "SBIN"}));
        jobs.start("TCS").unwrap(); // free again
    }

    #[tokio::test]
    async fn a_failing_command_reports_a_fixed_message() {
        let jobs = VerdictJobs::new(vec!["cmd".into(), "/C".into(), "exit 3 &".into()]);
        jobs.start("SBIN").unwrap();
        let s = finished(&jobs).await;
        assert_eq!(s["state"], "failed");
        assert_eq!(s["message"], "the verdict command failed (exit code 3)");
    }

    #[tokio::test]
    async fn a_missing_program_is_a_failure_not_a_panic() {
        let jobs = VerdictJobs::new(vec!["no-such-program-xyz".into()]);
        jobs.start("SBIN").unwrap();
        assert_eq!(finished(&jobs).await["message"], "could not start the verdict command");
    }

    #[tokio::test]
    async fn a_bad_symbol_never_starts() {
        let jobs = VerdictJobs::new(vec!["cmd".into()]);
        assert_eq!(jobs.start("a;b"), Err(StartError::BadSymbol));
        assert_eq!(jobs.status()["state"], "idle");
    }
}
