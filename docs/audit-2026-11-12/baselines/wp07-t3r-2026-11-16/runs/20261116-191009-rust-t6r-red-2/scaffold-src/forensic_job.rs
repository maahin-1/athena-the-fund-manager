//! The Analyse button of the IPO tab: one draft offer document goes in, one report document comes out (`athena.forensic.job`, schema `athena.forensic.report/1`).
//! Reading a draft takes minutes, so the page starts the job and polls it: one job at a time, a status that carries the finished report, and nothing from a child's
//! error output reaching the page. The address is checked here (https, the regulator's site, a PDF) and again by the Python job before anything is downloaded.
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tokio::process::Command;

pub const SCHEMA: &str = "athena.forensic.report/1";
const LIMIT: Duration = Duration::from_secs(1200);
const MAX_URL: usize = 400;
const MAX_NAME: usize = 120;
const MAX_ERROR: usize = 240;
const HOSTS: [&str; 2] = ["www.sebi.gov.in", "sebi.gov.in"];

/// Is this an https address of a PDF on the regulator's site, with no credentials, no port, no query tricks and no spaces?
pub fn url_ok(url: &str) -> bool {
    if url.is_empty() || url.len() > MAX_URL || url.chars().any(|c| c.is_whitespace() || c.is_control() || c == '\\' || c == '@' || c == '"' || c == '\'') {
        return false;
    }
    let Some(rest) = url.strip_prefix("https://") else { return false };
    let (host, path) = match rest.split_once('/') {
        Some((h, p)) => (h, p),
        None => return false,
    };
    if !HOSTS.contains(&host.to_ascii_lowercase().as_str()) {
        return false;
    }
    let path = path.split(['?', '#']).next().unwrap_or("");
    path.to_ascii_lowercase().ends_with(".pdf") && !path.contains("..")
}

/// A company name or a kind as the page sends it: plain words only.
pub fn name_ok(name: &str) -> bool {
    let n = name.trim();
    !n.is_empty() && n.chars().count() <= MAX_NAME && n.chars().all(|c| c.is_alphanumeric() || " &.,-()'/".contains(c))
}

#[derive(Debug, Clone, PartialEq)]
pub enum State {
    Idle,
    Running { url: String, since: Instant },
    Done { url: String, report: Value },
    Failed { url: String, message: String },
}

#[derive(Debug, PartialEq, Eq)]
pub enum StartError {
    BadRequest,
    /// Another document is being analysed.
    Busy(String),
}

/// How the job is started: the address, the company and the kind of document. Replaced in tests.
pub type Launcher = Arc<dyn Fn(&str, &str, &str) -> Command + Send + Sync>;

pub struct ForensicJobs {
    state: Mutex<State>,
    launch: Launcher,
}

impl ForensicJobs {
    pub fn new(launch: Launcher) -> Arc<Self> {
        Arc::new(ForensicJobs { state: Mutex::new(State::Idle), launch })
    }

    pub fn standard(python: &str, store_dir: std::path::PathBuf) -> Arc<Self> {
        let python = python.to_string();
        Self::new(Arc::new(move |url, company, kind| {
            let mut cmd = Command::new(&python);
            cmd.args(["-m", "athena.forensic.job", "--url", url, "--company", company, "--kind", kind]).arg("--docs").arg(store_dir.join("documents.duckdb")).arg("--forensic").arg(store_dir.join("forensic.duckdb"));
            cmd
        }))
    }

    /// What the page polls: idle, running (with the seconds so far), done (with the whole report) or failed (with a fixed message).
    pub fn status(&self) -> Value {
        match &*self.state.lock().unwrap() {
            State::Idle => json!({"state": "idle"}),
            State::Running { url, since } => json!({"state": "running", "url": url, "seconds": since.elapsed().as_secs()}),
            State::Done { url, report } => json!({"state": "done", "url": url, "report": report}),
            State::Failed { url, message } => json!({"state": "failed", "url": url, "error": message}),
        }
    }

    /// Start analysing `url` unless that is already running (the page just follows it) or another document is.
    pub fn start(self: &Arc<Self>, url: &str, company: &str, kind: &str) -> Result<(), StartError> {
        if !url_ok(url) || !name_ok(company) || !name_ok(kind) {
            return Err(StartError::BadRequest);
        }
        {
            let mut g = self.state.lock().unwrap();
            if let State::Running { url: running, .. } = &*g {
                return if running == url { Ok(()) } else { Err(StartError::Busy(running.clone())) };
            }
            *g = State::Running { url: url.to_string(), since: Instant::now() };
        }
        let (me, url, company, kind) = (self.clone(), url.to_string(), company.trim().to_string(), kind.trim().to_string());
        tokio::spawn(async move { me.run(url, company, kind).await });
        Ok(())
    }

    async fn run(self: Arc<Self>, url: String, company: String, kind: String) {
        let mut cmd = (self.launch)(&url, &company, &kind);
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        let outcome = match cmd.spawn() {
            Err(_) => Err("the analysis could not be started".to_string()),
            Ok(child) => match tokio::time::timeout(LIMIT, child.wait_with_output()).await {
                Err(_) => Err("the analysis took too long and was stopped".to_string()),
                Ok(Err(_)) => Err("the analysis failed".to_string()),
                Ok(Ok(out)) if !out.status.success() => Err("the analysis failed".to_string()),
                Ok(Ok(out)) => read_report(&out.stdout),
            },
        };
        *self.state.lock().unwrap() = match outcome {
            Ok(report) => State::Done { url, report },
            Err(message) => State::Failed { url, message },
        };
    }
}

/// The report document from the job's standard output: only a document of the schema this server knows, and either a report or the job's own short error.
pub fn read_report(stdout: &[u8]) -> Result<Value, String> {
    let doc: Value = serde_json::from_slice(stdout).map_err(|_| "the analysis gave an unreadable answer".to_string())?;
    if doc["schema"] != SCHEMA {
        return Err("the analysis gave an answer in a form this page does not know".into());
    }
    match doc["ok"].as_bool() {
        Some(true) => Ok(doc),
        _ => Err(doc["error"].as_str().unwrap_or("the analysis failed").chars().take(MAX_ERROR).collect()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/1_draft.pdf";

    fn python() -> Option<&'static str> {
        ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists())
    }

    /// A launcher that runs a Python one-liner printing `body`, so the whole path (spawn, read, parse) is exercised.
    fn printing(body: &str, exit: i32) -> Option<Launcher> {
        let py = python()?;
        let body = body.to_string();
        Some(Arc::new(move |_u, _c, _k| {
            let mut c = Command::new(py);
            c.args(["-c", &format!("import sys\nsys.stdout.write({body:?})\nsys.exit({exit})")]);
            c
        }))
    }

    async fn settled(j: &Arc<ForensicJobs>) -> Value {
        for _ in 0..400 {
            let s = j.status();
            if s["state"] != "running" {
                return s;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        j.status()
    }

    #[test]
    fn only_a_pdf_on_the_regulators_site_is_an_address_and_names_are_plain() {
        assert!(url_ok(GOOD) && url_ok("https://sebi.gov.in/a/b.PDF") && url_ok("https://www.sebi.gov.in/a.pdf?x=1"));
        for bad in [
            "", "http://www.sebi.gov.in/a.pdf", "https://evil.test/a.pdf", "https://www.sebi.gov.in.evil.test/a.pdf", "https://www.sebi.gov.in@evil.test/a.pdf", "https://user@www.sebi.gov.in/a.pdf",
            "https://www.sebi.gov.in:8443/a.pdf", "https://www.sebi.gov.in/a.html", "https://www.sebi.gov.in", "https://www.sebi.gov.in/../a.pdf", "https://www.sebi.gov.in/a b.pdf",
            "https://www.sebi.gov.in/a\\b.pdf", "file:///a.pdf", &format!("https://www.sebi.gov.in/{}.pdf", "x".repeat(400)),
        ] {
            assert!(!url_ok(bad), "{bad}");
        }
        assert!(name_ok("Deon Energy Limited") && name_ok("M&M Ltd.") && name_ok("DRHP"));
        for bad in ["", "  ", "a;b", "$(rm)", "x\ny", &"x".repeat(121)] {
            assert!(!name_ok(bad), "{bad}");
        }
    }

    #[test]
    fn nothing_is_started_for_a_bad_request() {
        let j = ForensicJobs::new(Arc::new(|_u, _c, _k| Command::new("no-such-program-xyz")));
        assert_eq!(j.start("https://evil.test/a.pdf", "Deon", "DRHP"), Err(StartError::BadRequest));
        assert_eq!(j.start(GOOD, "a;b", "DRHP"), Err(StartError::BadRequest));
        assert_eq!(j.start(GOOD, "Deon", ""), Err(StartError::BadRequest));
        assert_eq!(j.status(), json!({"state": "idle"}));
    }

    #[tokio::test]
    async fn a_report_of_the_known_schema_is_carried_whole_to_the_page() {
        let Some(launch) = printing(r#"{"schema": "athena.forensic.report/1", "ok": true, "checks": {"summary": {"held": 3}}}"#, 0) else { return };
        let j = ForensicJobs::new(launch);
        j.start(GOOD, "Deon Energy Limited", "DRHP").unwrap();
        assert_eq!(j.status()["state"], "running");
        let s = settled(&j).await;
        assert_eq!((s["state"].as_str(), s["url"].as_str()), (Some("done"), Some(GOOD)));
        assert_eq!(s["report"]["checks"]["summary"]["held"], 3);
    }

    #[tokio::test]
    async fn the_jobs_own_error_is_shown_short_and_anything_else_is_a_fixed_message() {
        let Some(own) = printing(&format!(r#"{{"schema": "athena.forensic.report/1", "ok": false, "error": "{}"}}"#, "e".repeat(500)), 0) else { return };
        let j = ForensicJobs::new(own);
        j.start(GOOD, "Deon", "DRHP").unwrap();
        assert_eq!(settled(&j).await["error"].as_str().unwrap().len(), MAX_ERROR);
        for (body, exit, message) in [("not json", 0, "unreadable"), (r#"{"schema": "something/9", "ok": true}"#, 0, "does not know"), (r#"{"schema": "athena.forensic.report/1", "ok": true}"#, 4, "failed")] {
            let Some(launch) = printing(body, exit) else { return };
            let j = ForensicJobs::new(launch);
            j.start(GOOD, "Deon", "DRHP").unwrap();
            let s = settled(&j).await;
            assert_eq!(s["state"], "failed");
            assert!(s["error"].as_str().unwrap().contains(message), "{s}");
        }
    }

    #[tokio::test]
    async fn a_program_that_cannot_start_is_a_failure_not_a_crash() {
        let j = ForensicJobs::new(Arc::new(|_u, _c, _k| Command::new("no-such-program-xyz")));
        j.start(GOOD, "Deon", "DRHP").unwrap();
        assert_eq!(settled(&j).await["error"], "the analysis could not be started");
    }

    #[tokio::test]
    async fn the_same_document_may_be_asked_twice_but_another_waits_and_the_arguments_reach_the_job() {
        let Some(py) = python() else { return };
        let seen = Arc::new(Mutex::new(Vec::<String>::new()));
        let log = seen.clone();
        let j = ForensicJobs::new(Arc::new(move |u, c, k| {
            log.lock().unwrap().push(format!("{u}|{c}|{k}"));
            let mut cmd = Command::new(py);
            cmd.args(["-c", "import time\ntime.sleep(2)"]);
            cmd
        }));
        j.start(GOOD, "Deon Energy Limited", "DRHP").unwrap();
        tokio::time::sleep(Duration::from_millis(300)).await; // the child is started by a task of its own
        assert_eq!(j.start(GOOD, "Deon Energy Limited", "DRHP"), Ok(()));
        assert_eq!(j.start("https://www.sebi.gov.in/other.pdf", "Other", "DRHP"), Err(StartError::Busy(GOOD.into())));
        assert_eq!(j.status()["url"], GOOD);
        assert_eq!(*seen.lock().unwrap(), [format!("{GOOD}|Deon Energy Limited|DRHP")]); // the second ask did not start a second child
    }
}
