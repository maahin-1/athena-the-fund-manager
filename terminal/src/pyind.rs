//! Python indicator files the user placed in a folder: listed (read, never run), switched on once by the user for the exact version on disk, and run in a child
//! process of their own with a time limit. The server never writes a `.py` file and no request can: the only file it writes is the list of what is switched on,
//! `.enabled.json` in the same folder, which holds names and hashes. THIS IS NOT A SANDBOX: a switched-on file is the user's own code and runs with the
//! user's rights; the page says so next to the switch.
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::AsyncWriteExt;
use tokio::process::Command;
use tokio::sync::{Mutex, Semaphore};

pub const RUN_LIMIT: Duration = Duration::from_secs(20);
const LIST_LIMIT: Duration = Duration::from_secs(15);
pub const MAX_BODY: usize = 4 * 1024 * 1024;
const MAX_REPLY: usize = 20 * 1024 * 1024;
const STATE_FILE: &str = ".enabled.json";

#[derive(Debug, PartialEq, Eq)]
pub enum PyIndError {
    /// The request is not acceptable (a file name that is not one, a hash that is not the file as it is, too much data).
    Bad(&'static str),
    /// The file is not switched on, or was changed after it was.
    NotEnabled,
    /// The indicator ran and failed, or broke the contract; the message is the runner's own.
    Failed(String),
    TimedOut,
    /// The runner could not be started or did not answer.
    Unavailable,
}

pub struct PyInd {
    python: String,
    runner: PathBuf,
    dir: PathBuf,
    limit: Duration,
    gate: Semaphore,
    state: Mutex<()>,
}

/// A file name the runner accepts: letters, digits, space, `_` and `-`, then `.py`, 1 to 60 characters before the extension.
pub fn file_ok(name: &str) -> bool {
    let Some(stem) = name.strip_suffix(".py") else { return false };
    (1..=60).contains(&stem.len()) && stem.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, ' ' | '_' | '-'))
}

fn hash_ok(sha: &str) -> bool {
    sha.len() == 64 && sha.chars().all(|c| c.is_ascii_hexdigit())
}

impl PyInd {
    pub fn new(python: &str, runner: &Path, dir: &Path) -> Arc<Self> {
        Self::with_limit(python, runner, dir, RUN_LIMIT)
    }

    pub fn with_limit(python: &str, runner: &Path, dir: &Path, limit: Duration) -> Arc<Self> {
        // the child runs in the indicators folder, so a relative path to Python or to the runner must be made absolute while it still means what it did
        let absolute = |p: &Path| std::path::absolute(p).unwrap_or_else(|_| p.to_path_buf());
        let python = if Path::new(python).components().count() > 1 { absolute(Path::new(python)).to_string_lossy().into_owned() } else { python.to_string() }; // a bare name is found on PATH
        Arc::new(PyInd { python, runner: absolute(runner), dir: absolute(dir), limit, gate: Semaphore::new(2), state: Mutex::new(()) })
    }

    fn command(&self, args: &[&str]) -> Command {
        let mut cmd = Command::new(&self.python);
        cmd.arg("-I").arg(&self.runner).args(args).env_clear();
        // Python on Windows will not start without these; nothing else of the server's environment reaches the indicator
        for key in ["SYSTEMROOT", "SystemRoot", "TEMP", "TMP"] {
            if let Ok(v) = std::env::var(key) {
                cmd.env(key, v);
            }
        }
        cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        cmd
    }

    async fn run_runner(&self, args: &[&str], input: &[u8], limit: Duration) -> Result<Value, PyIndError> {
        let mut cmd = self.command(args);
        if self.dir.is_dir() {
            cmd.current_dir(&self.dir);
        }
        let mut child = cmd.spawn().map_err(|_| PyIndError::Unavailable)?;
        if let Some(mut stdin) = child.stdin.take() {
            let input = input.to_vec();
            tokio::spawn(async move {
                let _ = stdin.write_all(&input).await;
            });
        }
        let out = match tokio::time::timeout(limit, child.wait_with_output()).await {
            Err(_) => return Err(PyIndError::TimedOut), // the child is killed when it is dropped
            Ok(Err(_)) => return Err(PyIndError::Unavailable),
            Ok(Ok(out)) => out,
        };
        if out.stdout.len() > MAX_REPLY {
            return Err(PyIndError::Failed("the indicator's result is too large".into()));
        }
        serde_json::from_slice(&out.stdout).map_err(|_| PyIndError::Unavailable)
    }

    fn state_path(&self) -> PathBuf {
        self.dir.join(STATE_FILE)
    }

    fn read_state(&self) -> serde_json::Map<String, Value> {
        std::fs::read_to_string(self.state_path())
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.get("enabled").and_then(|e| e.as_object().cloned()))
            .unwrap_or_default()
    }

    fn write_state(&self, enabled: &serde_json::Map<String, Value>) -> Result<(), PyIndError> {
        std::fs::create_dir_all(&self.dir).map_err(|_| PyIndError::Unavailable)?;
        let tmp = self.dir.join(".enabled.json.tmp");
        std::fs::write(&tmp, json!({"enabled": enabled}).to_string()).map_err(|_| PyIndError::Unavailable)?;
        std::fs::rename(&tmp, self.state_path()).map_err(|_| PyIndError::Unavailable)
    }

    /// The files in the folder with what the runner read from each (never by running it), and whether each is switched on for the version on disk.
    pub async fn list(&self) -> Result<Value, PyIndError> {
        let rows = self.run_runner(&["list", &self.dir.to_string_lossy()], b"", LIST_LIMIT).await?;
        let Value::Array(rows) = rows else { return Err(PyIndError::Unavailable) };
        let enabled = self.read_state();
        let indicators: Vec<Value> = rows
            .into_iter()
            .map(|mut row| {
                let file = row["file"].as_str().unwrap_or_default().to_string();
                let sha = row["sha256"].as_str().unwrap_or_default().to_string();
                let held = enabled.get(&file).and_then(|v| v.as_str());
                row["enabled"] = json!(held == Some(sha.as_str()) && row["error"].is_null());
                row["changed"] = json!(held.is_some_and(|h| h != sha));
                row
            })
            .collect();
        Ok(json!({"dir": self.dir.to_string_lossy(), "indicators": indicators}))
    }

    /// Switch a file on, for the version whose hash the user saw. A file that has changed since, or has an error, cannot be switched on.
    pub async fn enable(&self, file: &str, sha256: &str) -> Result<(), PyIndError> {
        if !file_ok(file) || !hash_ok(sha256) {
            return Err(PyIndError::Bad("that is not a file name and a hash"));
        }
        let _guard = self.state.lock().await;
        let listing = self.list().await?;
        let found = listing["indicators"].as_array().into_iter().flatten().any(|r| r["file"] == file && r["sha256"] == sha256 && r["error"].is_null());
        if !found {
            return Err(PyIndError::Bad("that is not the file as it is now (it changed, has an error, or is not there)"));
        }
        let mut enabled = self.read_state();
        enabled.insert(file.to_string(), json!(sha256));
        self.write_state(&enabled)
    }

    pub async fn disable(&self, file: &str) -> Result<(), PyIndError> {
        if !file_ok(file) {
            return Err(PyIndError::Bad("that is not a file name"));
        }
        let _guard = self.state.lock().await;
        let mut enabled = self.read_state();
        enabled.remove(file);
        self.write_state(&enabled)
    }

    /// Run a switched-on file over `bars` (a JSON list of {t, o, h, l, c, v}). The runner checks the hash again and runs the very bytes it hashed.
    pub async fn run(&self, file: &str, bars: &Value) -> Result<Value, PyIndError> {
        if !file_ok(file) {
            return Err(PyIndError::Bad("that is not a file name"));
        }
        let body = serde_json::to_vec(bars).map_err(|_| PyIndError::Bad("the bars are not JSON"))?;
        if body.len() > MAX_BODY || !bars.is_array() {
            return Err(PyIndError::Bad("the bars must be a list of at most 4 MB"));
        }
        let sha = self.read_state().get(file).and_then(|v| v.as_str().map(String::from)).ok_or(PyIndError::NotEnabled)?;
        let _permit = self.gate.acquire().await.map_err(|_| PyIndError::Unavailable)?;
        let reply = self.run_runner(&["run", &self.dir.to_string_lossy(), file, &sha], &body, self.limit).await?;
        match reply.get("error").and_then(Value::as_str) {
            Some(message) if message.contains("changed since it was switched on") => Err(PyIndError::NotEnabled),
            Some(message) => Err(PyIndError::Failed(message.to_string())),
            None => Ok(reply),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RUNNER: &str = "../src/athena/indicators_user/runner.py";
    const PYTHON: &str = "../.venv/Scripts/python.exe";
    const AVG: &str = "NAME = 'Avg'\nPANE = 'overlay'\ndef compute(bars):\n    return {'avg': [None if i < 1 else (bars[i]['c'] + bars[i-1]['c']) / 2 for i in range(len(bars))]}\n";

    fn setup(tag: &str, limit: Duration) -> Option<(Arc<PyInd>, PathBuf)> {
        if !Path::new(PYTHON).exists() || !Path::new(RUNNER).exists() {
            return None; // no project Python here: nothing to run
        }
        let dir = std::env::temp_dir().join(format!("athena-pyind-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Some((PyInd::with_limit(PYTHON, Path::new(RUNNER), &dir, limit), dir))
    }

    fn bars() -> Value {
        json!([{"t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1, "v": 10}, {"t": 2, "o": 1, "h": 2, "l": 0.5, "c": 3, "v": 10}, {"t": 3, "o": 1, "h": 2, "l": 0.5, "c": 5, "v": 10}])
    }

    fn sha(listing: &Value, file: &str) -> String {
        listing["indicators"].as_array().unwrap().iter().find(|r| r["file"] == file).unwrap()["sha256"].as_str().unwrap().to_string()
    }

    #[test]
    fn file_names_are_checked_the_way_the_runner_checks_them() {
        for good in ["a.py", "My indicator-2.py", "x_y.py"] {
            assert!(file_ok(good), "{good}");
        }
        for bad in ["", ".py", "a.txt", "../a.py", "a/b.py", "a\\b.py", "a$.py", &format!("{}.py", "x".repeat(61))] {
            assert!(!file_ok(bad), "{bad}");
        }
    }

    #[tokio::test]
    async fn a_file_is_listed_off_until_switched_on_for_the_version_the_user_saw_and_then_runs() {
        let Some((p, dir)) = setup("a", RUN_LIMIT) else { return };
        std::fs::write(dir.join("avg.py"), AVG).unwrap();
        let first = p.list().await.unwrap();
        let row = &first["indicators"][0];
        assert_eq!((row["name"].as_str(), row["pane"].as_str(), row["enabled"].as_bool(), row["changed"].as_bool()), (Some("Avg"), Some("overlay"), Some(false), Some(false)));
        assert_eq!(p.run("avg.py", &bars()).await, Err(PyIndError::NotEnabled)); // off until switched on
        assert!(matches!(p.enable("avg.py", &"0".repeat(64)).await, Err(PyIndError::Bad(_)))); // not the file as it is
        p.enable("avg.py", &sha(&first, "avg.py")).await.unwrap();
        assert_eq!(p.list().await.unwrap()["indicators"][0]["enabled"], true);
        assert_eq!(p.run("avg.py", &bars()).await.unwrap(), json!({"lines": {"avg": [null, 2.0, 4.0]}}));
    }

    #[tokio::test]
    async fn a_file_changed_after_it_was_switched_on_is_off_again_and_does_not_run() {
        let Some((p, dir)) = setup("b", RUN_LIMIT) else { return };
        std::fs::write(dir.join("avg.py"), AVG).unwrap();
        let hash = sha(&p.list().await.unwrap(), "avg.py");
        p.enable("avg.py", &hash).await.unwrap();
        std::fs::write(dir.join("avg.py"), AVG.replace("/ 2", "/ 3")).unwrap();
        let after = p.list().await.unwrap();
        assert_eq!((after["indicators"][0]["enabled"].as_bool(), after["indicators"][0]["changed"].as_bool()), (Some(false), Some(true)));
        assert_eq!(p.run("avg.py", &bars()).await, Err(PyIndError::NotEnabled));
        p.disable("avg.py").await.unwrap();
        assert_eq!(p.run("avg.py", &bars()).await, Err(PyIndError::NotEnabled));
    }

    #[tokio::test]
    async fn a_file_that_fails_says_how_and_one_that_hangs_is_stopped() {
        let Some((p, dir)) = setup("c", Duration::from_secs(2)) else { return };
        std::fs::write(dir.join("boom.py"), "def compute(bars):\n    raise RuntimeError('no good')\n").unwrap();
        std::fs::write(dir.join("hang.py"), "def compute(bars):\n    while True:\n        pass\n").unwrap();
        let listing = p.list().await.unwrap();
        p.enable("boom.py", &sha(&listing, "boom.py")).await.unwrap();
        p.enable("hang.py", &sha(&listing, "hang.py")).await.unwrap();
        match p.run("boom.py", &bars()).await {
            Err(PyIndError::Failed(m)) => assert!(m.contains("RuntimeError: no good"), "{m}"),
            other => panic!("{other:?}"),
        }
        let started = std::time::Instant::now();
        assert_eq!(p.run("hang.py", &bars()).await, Err(PyIndError::TimedOut));
        assert!(started.elapsed() < Duration::from_secs(10));
        assert!(p.run("boom.py", &bars()).await.is_err()); // the server went on
    }

    #[tokio::test]
    async fn the_servers_environment_does_not_reach_the_indicator_and_bad_requests_are_refused() {
        let Some((p, dir)) = setup("d", RUN_LIMIT) else { return };
        std::env::set_var("ATHENA_TEST_SECRET", "should-not-leak");
        std::fs::write(dir.join("env.py"), "import os\ndef compute(bars):\n    v = os.environ.get('ATHENA_TEST_SECRET')\n    return {'seen': [1.0 if v else 0.0] * len(bars)}\n").unwrap();
        p.enable("env.py", &sha(&p.list().await.unwrap(), "env.py")).await.unwrap();
        assert_eq!(p.run("env.py", &bars()).await.unwrap(), json!({"lines": {"seen": [0.0, 0.0, 0.0]}}));
        assert!(matches!(p.run("../x.py", &bars()).await, Err(PyIndError::Bad(_))));
        assert!(matches!(p.run("env.py", &json!("not a list")).await, Err(PyIndError::Bad(_))));
        assert!(matches!(p.enable("env.txt", &"0".repeat(64)).await, Err(PyIndError::Bad(_))));
        assert!(matches!(p.enable("env.py", "short").await, Err(PyIndError::Bad(_))));
    }

    #[tokio::test]
    async fn a_missing_folder_lists_nothing() {
        if !Path::new(PYTHON).exists() {
            return;
        }
        let p = PyInd::new(PYTHON, Path::new(RUNNER), Path::new("no/such/indicators"));
        assert_eq!(p.list().await.unwrap()["indicators"], json!([]));
    }
}
