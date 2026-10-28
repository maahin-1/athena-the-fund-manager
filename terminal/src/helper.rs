//! Long-running Python helpers (`athena.terminal_helper`) for the Fundamentals page, the valuation and the split-adjusted daily bars. Starting Python for
//! each request cost about 1.2 s of the 1.3 s a page took; a warm helper answers in 0.1 to 0.3 s. Each request carries the same arguments the command took and
//! the answer is what the command printed. A helper that fails, times out or gives an unreadable answer is dropped and the caller runs the command itself, as
//! before, so a broken helper costs speed and never an answer.
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;

/// Loading the modules takes a second or two; this is far more than that.
const START_LIMIT: Duration = Duration::from_secs(60);
/// How many helpers run side by side: the page and its valuation are asked for together.
pub const COUNT: usize = 2;

/// The helpers' shared error log, started afresh when it passes two megabytes so it never grows without end.
fn helper_log() -> Option<std::fs::File> {
    let path = std::path::Path::new(".athena").join("terminal").join("helper.log");
    std::fs::create_dir_all(path.parent()?).ok()?;
    if std::fs::metadata(&path).map(|m| m.len() > 2_000_000).unwrap_or(false) {
        let _ = std::fs::rename(&path, path.with_extension("old"));
    }
    std::fs::OpenOptions::new().create(true).append(true).open(path).ok()
}

struct Live {
    _child: Child, // kill_on_drop: dropping this ends the process
    stdin: ChildStdin,
    out: Lines<BufReader<ChildStdout>>,
}

pub struct Helpers {
    command: Vec<String>,
    slots: Vec<Mutex<Option<Live>>>,
    next: AtomicUsize,
    id: AtomicU64,
}

impl Helpers {
    pub fn new(command: Vec<String>, count: usize) -> Self {
        Helpers { command, slots: (0..count.max(1)).map(|_| Mutex::new(None)).collect(), next: AtomicUsize::new(0), id: AtomicU64::new(1) }
    }

    pub fn standard(python: &str) -> Self {
        Self::new(vec![python.into(), "-m".into(), "athena.terminal_helper".into()], COUNT)
    }

    async fn start(&self) -> Option<Live> {
        let mut cmd = Command::new(self.command.first()?);
        // what a helper writes to its error stream is kept in a log (a crash is no longer blind); an unwritable log is simply not used
        let stderr = helper_log().map(Stdio::from).unwrap_or_else(Stdio::null);
        cmd.args(&self.command[1..]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(stderr).kill_on_drop(true);
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

    /// Start every helper now, so the first page a person opens does not wait for one.
    pub async fn warm(&self) {
        for slot in &self.slots {
            let mut g = slot.lock().await;
            if g.is_none() {
                *g = self.start().await;
            }
        }
    }

    /// What the command `op` prints for `args`, or None when no helper could answer (the caller then runs the command itself).
    pub async fn run(&self, op: &str, args: &[String], limit: Duration) -> Option<Vec<u8>> {
        // a free helper if there is one, else wait for the next in turn
        let mut guard = None;
        for slot in &self.slots {
            if let Ok(g) = slot.try_lock() {
                guard = Some(g);
                break;
            }
        }
        let mut guard = match guard {
            Some(g) => g,
            None => self.slots[self.next.fetch_add(1, Ordering::Relaxed) % self.slots.len()].lock().await,
        };
        if guard.is_none() {
            *guard = Some(self.start().await?);
        }
        let id = self.id.fetch_add(1, Ordering::Relaxed);
        let request = json!({"id": id, "op": op, "args": args}).to_string() + "\n";
        let live = guard.as_mut().expect("started above");
        let reply = tokio::time::timeout(limit, async {
            live.stdin.write_all(request.as_bytes()).await.ok()?;
            live.stdin.flush().await.ok()?;
            while let Ok(Some(line)) = live.out.next_line().await {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    if v["id"] == id {
                        return Some(v);
                    }
                }
            }
            None
        })
        .await
        .ok()
        .flatten();
        match reply {
            Some(v) if v["ok"] == true => v["out"].as_str().map(|s| s.as_bytes().to_vec()),
            Some(_) => None, // the helper is fine; the command itself failed, and running it directly reports that its own way
            None => {
                *guard = None; // dead, stuck or silent: the next request starts a new one
                None
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
        assert_eq!(h.run("page", &args, Duration::from_secs(10)).await.as_deref(), Some(&b"TCS --price 3200"[..]));
        assert_eq!(h.run("valuation", &args, Duration::from_secs(10)).await, None);
        assert_eq!(h.run("chart_bars", &args, Duration::from_millis(300)).await, None);
        // the silent one was dropped and its slot starts afresh
        assert_eq!(h.run("page", &["X".to_string()], Duration::from_secs(10)).await.as_deref(), Some(&b"X"[..]));
    }

    #[tokio::test]
    async fn a_helper_that_cannot_start_is_none() {
        let h = Helpers::new(vec!["no-such-program-xyz".into()], 1);
        assert_eq!(h.run("page", &[], Duration::from_secs(1)).await, None);
    }
}
