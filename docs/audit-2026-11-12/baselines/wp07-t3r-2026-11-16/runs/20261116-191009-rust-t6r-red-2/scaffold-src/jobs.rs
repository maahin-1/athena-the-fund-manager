//! The background data jobs. The terminal starts one Python supervisor (`athena.ops.supervisor`) when it starts; that supervisor runs the data jobs when they are due, one at a time, and ends when
//! this process does (it is given our pid). Nothing is registered with the operating system. `/api/jobs` shows what the supervisor and the jobs have written to their status files.

use std::path::Path;
use std::process::{Command, Stdio};

use serde_json::{json, Value};

/// Start the supervisor. A second one (the terminal restarted while the old one was still ending) waits for the first to go and then takes over, or gives up: either way this never blocks.
pub fn spawn_supervisor(python: &str) -> std::io::Result<std::process::Child> {
    let mut cmd = Command::new(python);
    cmd.args(["-m", "athena.ops.supervisor", "--parent-pid", &std::process::id().to_string()]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn()
}

fn read_json(path: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// What the supervisor knows (`supervisor.json`) and every job's status file, as one value.
pub fn read_jobs(supervisor: &Path, status_dir: &Path) -> Value {
    let mut jobs: Vec<Value> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(status_dir) {
        let mut paths: Vec<_> = entries.flatten().map(|e| e.path()).filter(|p| p.extension().map(|x| x == "json").unwrap_or(false)).collect();
        paths.sort();
        jobs.extend(paths.iter().filter_map(|p| read_json(p)));
    }
    json!({"supervisor": read_json(supervisor).unwrap_or(Value::Null), "jobs": jobs})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_supervisor_and_every_status_file_and_survives_missing_or_damaged_ones() {
        let dir = std::env::temp_dir().join(format!("athena-jobs-{}", std::process::id()));
        let status = dir.join("status");
        std::fs::create_dir_all(&status).unwrap();
        std::fs::write(dir.join("supervisor.json"), r#"{"running":"prices"}"#).unwrap();
        std::fs::write(status.join("a.json"), r#"{"name":"a","state":"running"}"#).unwrap();
        std::fs::write(status.join("b.json"), "{damaged").unwrap();
        std::fs::write(status.join("notes.txt"), "ignored").unwrap();
        let v = read_jobs(&dir.join("supervisor.json"), &status);
        assert_eq!(v["supervisor"]["running"], "prices");
        assert_eq!(v["jobs"].as_array().unwrap().len(), 1);
        assert_eq!(v["jobs"][0]["name"], "a");
        let none = read_jobs(&dir.join("absent.json"), &dir.join("absent"));
        assert!(none["supervisor"].is_null() && none["jobs"].as_array().unwrap().is_empty());
        std::fs::remove_dir_all(&dir).ok();
    }
}
