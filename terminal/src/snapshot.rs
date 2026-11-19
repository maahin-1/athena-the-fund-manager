//! Reading the warehouse while a long job is writing it, and saying which data answered (audit T5, WP07).
//!
//! DuckDB lets one process write or many read, never both, so a build that holds `warehouse.duckdb` for an hour used to turn every chart and every statement into "warehouse busy". Before a job that
//! writes the warehouse starts, the supervisor (`athena.ops.snapshot`) publishes a *generation* of the file beside it. A reader opens the real file and, when that cannot be used, opens a generation
//! **it has validated**, and reports which: it never returns a snapshot it could not validate, and it never invents an identity for one.
//!
//! The protocol is written in `src/athena/warehouse/generations.py`, which is the specification this file implements. Beside `X.duckdb`: `X.snap.<gen>.duckdb` (the copy), `X.snap.<gen>.json` (the
//! manifest that pins it: generation id, file name, byte size, SHA-1, creation time, the data's own dates), both immutable once published, and `X.snap.current` (the pointer, the only file replaced, and
//! replaced atomically). `X.snap.status.json` records the last publication attempt and is read for display only: it never decides which generation is used. A reader reads the pointer once and then uses
//! only the immutable files of the generation it names; pointer, manifest, file name, size and content hash must all agree, or the result is an explicit failure. A generation deleted between the
//! pointer read and its use (cleanup racing the reader) is retried once through a fresh pointer read.
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use duckdb::{AccessMode, Config, Connection};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};

pub const MANIFEST_SCHEMA: &str = "athena.snapshot-manifest/1";
pub const POINTER_SCHEMA: &str = "athena.snapshot-pointer/1";
const READ_RETRIES: usize = 6;
const READ_RETRY_SLEEP: Duration = Duration::from_millis(20);
/// How long a reader's last answer counts when deciding whether the panels are on different generations.
pub const MIXED_WINDOW_SECS: u64 = 600;

/// `…/warehouse.duckdb` -> `…/warehouse.snap.duckdb`: the single-file copy of the earlier scheme. It is no longer read (it has no manifest); the name is kept so the failure can say it was ignored.
pub fn snapshot_path(path: &Path) -> PathBuf {
    path.with_extension("snap.duckdb")
}

fn stem(path: &Path) -> String {
    path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
}

pub fn pointer_path(path: &Path) -> PathBuf {
    path.with_file_name(format!("{}.snap.current", stem(path)))
}

pub fn status_path(path: &Path) -> PathBuf {
    path.with_file_name(format!("{}.snap.status.json", stem(path)))
}

pub fn data_name(path: &Path, gen: &str) -> String {
    format!("{}.snap.{}.duckdb", stem(path), gen)
}

pub fn manifest_name(path: &Path, gen: &str) -> String {
    format!("{}.snap.{}.json", stem(path), gen)
}

/// `20261115T101500Z-a1b2c3`: a date, `T`, a time, `Z`, `-` and six lower-case hex digits. A pointer naming anything else could be steering a reader to another file.
pub fn generation_id_ok(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 23
        && b[..8].iter().all(u8::is_ascii_digit)
        && b[8] == b'T'
        && b[9..15].iter().all(u8::is_ascii_digit)
        && b[15] == b'Z'
        && b[16] == b'-'
        && b[17..].iter().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(c))
}

/// Why no generation can be returned. Each is explicit; none means "empty".
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SnapshotFailure {
    /// No snapshot has been published (or only the earlier scheme's unmanifested copy exists, which is not read).
    Absent(String),
    PointerInvalid(String),
    ManifestInvalid(String),
    Mismatch(String),
    SizeMismatch(String),
    HashMismatch(String),
    /// A file of the generation the pointer names was removed (cleanup raced the read) and a second read of the pointer found no usable replacement.
    Vanished(String),
    /// A small file stayed unreadable (on Windows, briefly locked while being replaced) after a few tries.
    Unreadable(String),
    /// The validated generation could not be opened by DuckDB.
    OpenFailed(String),
}

impl SnapshotFailure {
    pub fn reason(&self) -> &'static str {
        match self {
            SnapshotFailure::Absent(_) => "absent",
            SnapshotFailure::PointerInvalid(_) => "pointer_invalid",
            SnapshotFailure::ManifestInvalid(_) => "manifest_invalid",
            SnapshotFailure::Mismatch(_) => "mismatch",
            SnapshotFailure::SizeMismatch(_) => "size_mismatch",
            SnapshotFailure::HashMismatch(_) => "hash_mismatch",
            SnapshotFailure::Vanished(_) => "vanished",
            SnapshotFailure::Unreadable(_) => "unreadable",
            SnapshotFailure::OpenFailed(_) => "open_failed",
        }
    }

    pub fn detail(&self) -> &str {
        match self {
            SnapshotFailure::Absent(d)
            | SnapshotFailure::PointerInvalid(d)
            | SnapshotFailure::ManifestInvalid(d)
            | SnapshotFailure::Mismatch(d)
            | SnapshotFailure::SizeMismatch(d)
            | SnapshotFailure::HashMismatch(d)
            | SnapshotFailure::Vanished(d)
            | SnapshotFailure::Unreadable(d)
            | SnapshotFailure::OpenFailed(d) => d,
        }
    }
}

/// A generation whose pointer, manifest, file name, size and content hash agree.
#[derive(Debug, Clone)]
pub struct Validated {
    pub generation_id: String,
    pub path: PathBuf,
    pub manifest: Value,
}

enum Step {
    /// A file that should exist is gone: a race with cleanup, or no snapshot at all.
    Missing(String),
    Failed(SnapshotFailure),
}

fn read_text(path: &Path) -> std::io::Result<String> {
    let mut last = None;
    for attempt in 0..READ_RETRIES {
        match std::fs::read_to_string(path) {
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                last = Some(e);
                if attempt + 1 < READ_RETRIES {
                    std::thread::sleep(READ_RETRY_SLEEP);
                }
            }
            other => return other,
        }
    }
    Err(last.unwrap())
}

fn load_json(path: &Path, bad: fn(String) -> SnapshotFailure) -> Result<Value, Step> {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let text = read_text(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => Step::Missing(name.clone()),
        std::io::ErrorKind::PermissionDenied => Step::Failed(SnapshotFailure::Unreadable(format!("{name} stayed unreadable"))),
        _ => Step::Failed(bad(format!("{name} cannot be read"))),
    })?;
    match serde_json::from_str::<Value>(&text) {
        Ok(v) if v.is_object() => Ok(v),
        Ok(_) => Err(Step::Failed(bad(format!("{name} is not an object")))),
        Err(_) => Err(Step::Failed(bad(format!("{name} is not valid JSON")))),
    }
}

fn str_field<'a>(v: &'a Value, key: &str) -> Option<&'a str> {
    v.get(key).and_then(Value::as_str)
}

fn u64_field(v: &Value, key: &str) -> Option<u64> {
    v.get(key).and_then(Value::as_u64)
}

fn sha1_of(path: &Path) -> std::io::Result<String> {
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha1::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

type VerifiedKey = (PathBuf, u64, u128);

fn verified() -> &'static Mutex<HashMap<VerifiedKey, String>> {
    static V: OnceLock<Mutex<HashMap<VerifiedKey, String>>> = OnceLock::new();
    V.get_or_init(|| Mutex::new(HashMap::new()))
}

fn mtime_nanos(meta: &std::fs::Metadata) -> u128 {
    meta.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_nanos()).unwrap_or(0)
}

/// One attempt at the current generation. `after_pointer` runs between the pointer read and the use of the generation (a test hook for a pointer that moves, or cleanup that runs, in that window).
fn once(main: &Path, verify_hash: bool, after_pointer: Option<&dyn Fn()>) -> Result<Validated, Step> {
    let pointer = load_json(&pointer_path(main), SnapshotFailure::PointerInvalid)?;
    let (gen, file, size, sha1) = (str_field(&pointer, "generation_id"), str_field(&pointer, "file"), u64_field(&pointer, "size"), str_field(&pointer, "sha1"));
    let (Some(gen), Some(file), Some(size), Some(sha1)) = (gen, file, size, sha1) else {
        return Err(Step::Failed(SnapshotFailure::PointerInvalid("the pointer does not have the expected fields".into())));
    };
    if str_field(&pointer, "schema") != Some(POINTER_SCHEMA) || !generation_id_ok(gen) {
        return Err(Step::Failed(SnapshotFailure::PointerInvalid("the pointer has an unexpected schema or generation id".into())));
    }
    if file != data_name(main, gen) {
        return Err(Step::Failed(SnapshotFailure::Mismatch(format!("the pointer names {file:?} for generation {gen}, not {:?}", data_name(main, gen)))));
    }
    if let Some(hook) = after_pointer {
        hook();
    }
    // the manifest of a generation never changes, so a later pointer move cannot make this pair inconsistent
    let manifest = load_json(&main.with_file_name(manifest_name(main, gen)), SnapshotFailure::ManifestInvalid)?;
    if str_field(&manifest, "schema") != Some(MANIFEST_SCHEMA) {
        return Err(Step::Failed(SnapshotFailure::ManifestInvalid("the manifest has an unexpected schema".into())));
    }
    if str_field(&manifest, "generation_id") != Some(gen) || str_field(&manifest, "file") != Some(file) {
        return Err(Step::Failed(SnapshotFailure::Mismatch("the manifest names a different generation or file than the pointer".into())));
    }
    let (Some(m_size), Some(m_sha1)) = (u64_field(&manifest, "size"), str_field(&manifest, "sha1")) else {
        return Err(Step::Failed(SnapshotFailure::ManifestInvalid("the manifest does not give a size and a SHA-1".into())));
    };
    if m_size != size || m_sha1 != sha1 {
        return Err(Step::Failed(SnapshotFailure::Mismatch("the pointer and the manifest disagree on the size or the content hash".into())));
    }
    let data = main.with_file_name(file);
    let meta = std::fs::metadata(&data).map_err(|e| if e.kind() == std::io::ErrorKind::NotFound { Step::Missing(file.to_string()) } else { Step::Failed(SnapshotFailure::Unreadable(format!("{file} cannot be examined"))) })?;
    if meta.len() != m_size {
        return Err(Step::Failed(SnapshotFailure::SizeMismatch(format!("{file} is {} bytes; the manifest says {m_size}", meta.len()))));
    }
    if verify_hash {
        let key = (data.clone(), meta.len(), mtime_nanos(&meta));
        let known = verified().lock().unwrap().get(&key).map(|h| h == m_sha1).unwrap_or(false);
        if !known {
            let actual = sha1_of(&data).map_err(|e| if e.kind() == std::io::ErrorKind::NotFound { Step::Missing(file.to_string()) } else { Step::Failed(SnapshotFailure::Unreadable(format!("{file} cannot be read"))) })?;
            if actual != m_sha1 {
                return Err(Step::Failed(SnapshotFailure::HashMismatch(format!("{file} does not have the content hash its manifest records"))));
            }
            verified().lock().unwrap().insert(key, m_sha1.to_string());
        }
    }
    Ok(Validated { generation_id: gen.to_string(), path: data, manifest })
}

/// The validated current generation of `main`'s snapshot, or an explicit failure.
pub fn resolve(main: &Path) -> Result<Validated, SnapshotFailure> {
    resolve_with(main, true, None)
}

/// As `resolve`, with the content-hash check optional and a hook between the pointer read and the use of the generation (tests).
pub fn resolve_with(main: &Path, verify_hash: bool, after_pointer: Option<&dyn Fn()>) -> Result<Validated, SnapshotFailure> {
    let mut last = String::new();
    for attempt in 0..2 {
        match once(main, verify_hash, if attempt == 0 { after_pointer } else { None }) {
            Ok(v) => return Ok(v),
            Err(Step::Failed(f)) => return Err(f),
            Err(Step::Missing(name)) => {
                if !pointer_path(main).exists() {
                    let legacy = if snapshot_path(main).is_file() { " (a single-file copy of the earlier scheme exists but has no manifest and is not used; the next job's snapshot replaces it)" } else { "" };
                    return Err(SnapshotFailure::Absent(format!("no snapshot has been published{legacy}")));
                }
                last = name; // a file the pointer names is gone: cleanup raced this read; read the pointer again once
            }
        }
    }
    Err(SnapshotFailure::Vanished(format!("a file of the generation named by the pointer was removed ({last})")))
}

/// Open the validated current generation read-only. A generation that disappears between validation and opening is retried once through a fresh pointer read.
pub fn open_snapshot(main: &Path) -> Result<(Connection, Validated), SnapshotFailure> {
    open_snapshot_with(main, None)
}

pub fn open_snapshot_with(main: &Path, after_validate: Option<&dyn Fn()>) -> Result<(Connection, Validated), SnapshotFailure> {
    let mut last = SnapshotFailure::Vanished("the generation was removed before it could be opened".into());
    for attempt in 0..2 {
        let v = resolve(main)?;
        if let (0, Some(hook)) = (attempt, after_validate) {
            hook();
        }
        let config = Config::default().access_mode(AccessMode::ReadOnly).map_err(|e| SnapshotFailure::OpenFailed(e.to_string()))?;
        match Connection::open_with_flags(&v.path, config) {
            Ok(conn) => return Ok((conn, v)),
            Err(e) => {
                if v.path.exists() {
                    return Err(SnapshotFailure::OpenFailed(e.to_string()));
                }
                last = SnapshotFailure::Vanished(format!("{} was removed before it could be opened", v.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()));
            }
        }
    }
    Err(last)
}

/// DuckDB's own wording for a file another process holds, and Windows' for a file in use. A lock is recognised by these, never by the bare word "lock", which a path can contain (`clock.duckdb`, `blocks/`).
const LOCK_PHRASES: [&str; 5] = ["could not set lock on file", "conflicting lock is held", "being used by another process", "file is already open in", "sharing violation"];

/// `text` lower-cased with every file path taken out: the quoted ones DuckDB prints and any occurrence of the file's own path, directory or name. What is left is the wording of the failure.
fn without_paths(text: &str, path: &Path) -> String {
    let mut t = text.to_ascii_lowercase();
    let mut names: Vec<String> = Vec::new();
    for p in [Some(path), path.parent()].into_iter().flatten() {
        let s = p.to_string_lossy().to_string();
        names.extend([s.replace('\\', "/"), s.replace('/', "\\"), s]);
    }
    if let Some(n) = path.file_name() {
        names.push(n.to_string_lossy().into_owned());
    }
    names.retain(|n| n.chars().count() >= 3);
    names.sort_by_key(|n| std::cmp::Reverse(n.len()));
    names.dedup();
    for n in names {
        t = t.replace(&n.to_ascii_lowercase(), " ");
    }
    let mut out = String::with_capacity(t.len());
    let mut quoted = false;
    for c in t.chars() {
        if c == '"' {
            quoted = !quoted;
            out.push(' ');
        } else if !quoted {
            out.push(c);
        }
    }
    out
}

/// Why the real file could not be used: `main_missing`, `locked_by_writer` (the expected case) or `main_unreadable` (anything else, damage included). `exists` is whether `path` exists.
/// A corrupt `clock.duckdb`, or a file under a directory named `blocks`, is not a writer: only the failure's wording counts.
pub fn classify_open_error(exists: bool, path: &Path, message: &str) -> &'static str {
    if !exists {
        return "main_missing";
    }
    let m = without_paths(message, path);
    if LOCK_PHRASES.iter().any(|k| m.contains(k)) {
        "locked_by_writer"
    } else {
        "main_unreadable"
    }
}

/// Which data answered: the real file, or a validated snapshot generation and why the real file was not used.
#[derive(Debug, Clone, PartialEq)]
pub struct Provenance {
    /// `main` or `snapshot`.
    pub source: &'static str,
    pub source_path: String,
    /// A snapshot's id comes from its manifest. A main file's is `main:<size>:<modified ns>`: what was observed of it, not a version the database vouches for.
    pub generation_id: String,
    /// `manifest` or `observed`.
    pub generation_basis: &'static str,
    /// When the copy was taken. Never a statement about how fresh its data is: that is `data_as_of`.
    pub snapshot_created_at: Option<String>,
    /// The dates the data stops at, per dataset, from the manifest (null for the real file; an endpoint states the date of what it returned).
    pub data_as_of: Value,
    pub fallback_reason: Option<&'static str>,
    pub validated: bool,
    /// The last publication attempt (published or failed at which stage), read for display only.
    pub snapshot_status: Value,
    pub main_error: Option<String>,
}

impl Provenance {
    pub fn to_value(&self) -> Value {
        json!({
            "source": self.source, "source_path": self.source_path, "generation_id": self.generation_id, "generation_basis": self.generation_basis,
            "snapshot_created_at": self.snapshot_created_at, "data_as_of": self.data_as_of, "fallback_reason": self.fallback_reason, "validated": self.validated,
            "snapshot_status": self.snapshot_status, "main_error": self.main_error,
        })
    }

    fn main(path: &Path) -> Provenance {
        let id = match std::fs::metadata(path) {
            Ok(m) => format!("main:{}:{}", m.len(), mtime_nanos(&m)),
            Err(_) => "main:unobserved".to_string(),
        };
        Provenance { source: "main", source_path: path.to_string_lossy().into_owned(), generation_id: id, generation_basis: "observed", snapshot_created_at: None, data_as_of: Value::Null, fallback_reason: None, validated: false, snapshot_status: Value::Null, main_error: None }
    }

    fn snapshot(main: &Path, v: &Validated, reason: &'static str, main_error: String) -> Provenance {
        Provenance {
            source: "snapshot",
            source_path: v.path.to_string_lossy().into_owned(),
            generation_id: v.generation_id.clone(),
            generation_basis: "manifest",
            snapshot_created_at: str_field(&v.manifest, "created_at").map(str::to_string),
            data_as_of: v.manifest.get("data_as_of").cloned().unwrap_or(Value::Null),
            fallback_reason: Some(reason),
            validated: true,
            snapshot_status: load_json(&status_path(main), SnapshotFailure::ManifestInvalid).unwrap_or(Value::Null), // advisory: an unreadable status is shown as absent, never used to pick a generation
            main_error: Some(main_error),
        }
    }
}

pub struct Opened {
    pub conn: Connection,
    pub provenance: Provenance,
}

/// Nothing could be opened: the real file failed for `main_reason`, and no validated snapshot could replace it (`snapshot`).
pub struct OpenFailure {
    pub main_reason: &'static str,
    pub main_error: String,
    pub snapshot: SnapshotFailure,
    /// The error `open_read_only` has always returned: the real file's own.
    pub main: duckdb::Error,
}

impl OpenFailure {
    pub fn to_value(&self) -> Value {
        json!({"failure": {"main_reason": self.main_reason, "main_error": self.main_error, "snapshot_reason": self.snapshot.reason(), "snapshot_detail": self.snapshot.detail()}})
    }
}

/// Open read-only; if the real file cannot be used, open its validated snapshot generation. Which one answered, and why, is in the provenance. Nothing is registered: a caller that opens the
/// warehouse to *build* something it then keeps (an index, a cache) registers what it actually serves, at the point it serves it (`record_served`), not what it last opened.
/// The real file's behaviour is as it always was: it is tried first and used whenever it opens.
pub fn open_unrecorded(path: &Path) -> Result<Opened, OpenFailure> {
    let config = match Config::default().access_mode(AccessMode::ReadOnly) {
        Ok(c) => c,
        Err(e) => return Err(OpenFailure { main_reason: "main_unreadable", main_error: e.to_string(), snapshot: SnapshotFailure::Absent("not tried".into()), main: e }),
    };
    match Connection::open_with_flags(path, config) {
        Ok(conn) => Ok(Opened { conn, provenance: Provenance::main(path) }),
        Err(err) => {
            let reason = classify_open_error(path.exists(), path, &err.to_string());
            let main_error: String = err.to_string().chars().take(200).collect();
            match open_snapshot(path) {
                Ok((conn, v)) => Ok(Opened { conn, provenance: Provenance::snapshot(path, &v, reason, main_error) }),
                Err(snapshot) => Err(OpenFailure { main_reason: reason, main_error, snapshot, main: err }),
            }
        }
    }
}

/// The failure value of a warehouse that opened but whose content could not be used (a table missing, a query refused), in the shape the registry reports: `what` says which.
pub fn unreadable_content(what: &str) -> Value {
    json!({"failure": {"main_reason": "unreadable_content", "main_error": what, "snapshot_reason": "not_tried", "snapshot_detail": format!("the warehouse opened but {what}")}})
}

/// `open_unrecorded`, registering the answer under `consumer` (a reader that answers from what it opens, once per request: analysis, the screen, the calendar). `consumer` names the reader for `data_sources`.
pub fn open(path: &Path, consumer: &str) -> Result<Opened, OpenFailure> {
    let opened = open_unrecorded(path);
    match &opened {
        Ok(o) => record_served(consumer, None, o.provenance.to_value()),
        Err(f) => record_served(consumer, None, f.to_value()),
    }
    opened
}

/// `open` without a named reader, returning what this function has always returned: the connection, or the real file's own error when nothing could be opened.
pub fn open_read_only(path: &Path) -> duckdb::Result<Connection> {
    open(path, "unnamed").map(|o| o.conn).map_err(|f| f.main)
}

// --- what was served, for the API and the page --------------------------------------------------------------------------------------------------------------------------------------------------
/// The most entries the registry keeps; the one least recently served is dropped beyond that.
pub const MAX_ENTRIES: usize = 128;

struct Entry {
    at: u64,
    reader: String,
    subject: Option<String>,
    value: Value,
}

/// What the terminal served, by reader and subject (`bars:SBIN:adjusted`, `search`, `calendar`): the provenance of the answer **as it was given**, renewed every time it is given again (a cache hit
/// included), so an entry describes data a person can be looking at now rather than the last time something was opened. A failed refresh of data that is still being served is kept apart
/// (`refresh_failures`), never written over the provenance of what was served.
///
/// The key is the reader and the subject the server can see (a symbol and a variant), **not a browser panel**: the server cannot tell two panels showing the same symbol apart, and says only that
/// the answers it recently gave for different subjects came from different generations.
#[derive(Default)]
pub struct Registry {
    served: Mutex<BTreeMap<String, Entry>>,
    refresh: Mutex<BTreeMap<String, (u64, Value)>>,
}

fn key_of(reader: &str, subject: Option<&str>) -> String {
    subject.map_or_else(|| reader.to_string(), |s| format!("{reader}:{s}"))
}

impl Registry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record_served_at(&self, now: u64, reader: &str, subject: Option<&str>, value: Value) {
        let key = key_of(reader, subject);
        let mut g = self.served.lock().unwrap();
        g.insert(key.clone(), Entry { at: now, reader: reader.to_string(), subject: subject.map(str::to_string), value });
        while g.len() > MAX_ENTRIES {
            let oldest = g.iter().filter(|(k, _)| **k != key).min_by_key(|(_, e)| e.at).map(|(k, _)| k.clone());
            match oldest {
                Some(k) => {
                    g.remove(&k);
                }
                None => break,
            }
        }
    }

    pub fn record_refresh_failure_at(&self, now: u64, reader: &str, value: Value) {
        self.refresh.lock().unwrap().insert(reader.to_string(), (now, value));
    }

    pub fn clear_refresh_failure(&self, reader: &str) {
        self.refresh.lock().unwrap().remove(reader);
    }

    pub fn len(&self) -> usize {
        self.served.lock().unwrap().len()
    }

    /// What was last registered under `reader` (no subject) as served, and as a refresh failure, exactly as stored. For tests that replay a real reader's entries into a registry with a fixed clock.
    #[cfg(test)]
    pub(crate) fn raw(&self, reader: &str) -> (Option<Value>, Option<Value>) {
        (self.served.lock().unwrap().get(reader).map(|e| e.value.clone()), self.refresh.lock().unwrap().get(reader).map(|(_, v)| v.clone()))
    }

    /// The report: every entry with its age and whether it is recent (served within `window` seconds), the generations the recent ones are on, whether those differ, and the refresh failures.
    pub fn summarize(&self, now: u64, window: u64) -> Value {
        let served = self.served.lock().unwrap();
        let recent = |e: &Entry| now.saturating_sub(e.at) <= window;
        let recent_ids: BTreeSet<String> = served.values().filter(|e| recent(e)).filter_map(|e| e.value.get("generation_id").and_then(Value::as_str).map(str::to_string)).collect();
        let readers: BTreeMap<String, Value> = served
            .iter()
            .map(|(k, e)| {
                let mut v = e.value.clone();
                if let Some(o) = v.as_object_mut() {
                    o.insert("reader".into(), json!(e.reader));
                    o.insert("subject".into(), json!(e.subject));
                    o.insert("at".into(), json!(e.at));
                    o.insert("age_seconds".into(), json!(now.saturating_sub(e.at)));
                    o.insert("recent".into(), json!(recent(e)));
                }
                (k.clone(), v)
            })
            .collect();
        let refresh_failures: BTreeMap<String, Value> = self
            .refresh
            .lock()
            .unwrap()
            .iter()
            .map(|(k, (at, v))| {
                let mut v = v.clone();
                if let Some(o) = v.as_object_mut() {
                    // the same clock and window as a served entry: a failure nothing has repeated for longer than `window` is history, not current activity
                    o.insert("reader".into(), json!(k));
                    o.insert("at".into(), json!(at));
                    o.insert("age_seconds".into(), json!(now.saturating_sub(*at)));
                    o.insert("recent".into(), json!(now.saturating_sub(*at) <= window));
                }
                (k.clone(), v)
            })
            .collect();
        json!({"readers": readers, "recent_generations": recent_ids, "mixed_generations": recent_ids.len() > 1, "window_seconds": window, "refresh_failures": refresh_failures, "snapshot_publication": Value::Null})
    }
}

fn global() -> &'static Registry {
    static R: OnceLock<Registry> = OnceLock::new();
    R.get_or_init(Registry::new)
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Register an answer as it was served (renewing the entry): `provenance` is what the response itself carried.
pub fn record_served(reader: &str, subject: Option<&str>, provenance: Value) {
    global().record_served_at(now_secs(), reader, subject, provenance);
}

/// A refresh of data that is still being served failed: kept apart from the provenance of what is served (see `Registry`).
pub fn record_refresh_failure(reader: &str, failure: Value) {
    global().record_refresh_failure_at(now_secs(), reader, failure);
}

pub fn clear_refresh_failure(reader: &str) {
    global().clear_refresh_failure(reader);
}

/// The last publication attempt (published, or failed at which stage), from the status file beside `path`. Advisory: absent or unreadable is `null`.
pub fn publication_status(path: &Path) -> Value {
    load_json(&status_path(path), SnapshotFailure::ManifestInvalid).unwrap_or(Value::Null)
}

/// What the terminal recently served from the warehouse (the real file or which snapshot generation, and why), whether those answers came from different generations, the failed refreshes, and the
/// last publication status of the snapshot beside `path`. A page that mixes generations says so.
pub fn data_sources_for(path: &Path) -> Value {
    data_sources_for_at(path, now_secs())
}

pub fn data_sources_for_at(path: &Path, now: u64) -> Value {
    let mut v = global().summarize(now, MIXED_WINDOW_SECS);
    v["snapshot_publication"] = publication_status(path);
    v
}

/// As `data_sources_for_at`, without a warehouse path (no publication status).
pub fn data_sources() -> Value {
    global().summarize(now_secs(), MIXED_WINDOW_SECS)
}

/// A writer for the tests, following the protocol of `generations.py`, and synthetic warehouses. Test code only.
#[cfg(test)]
pub(crate) mod testkit {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    pub fn temp_dir(tag: &str) -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!("athena-snap-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A warehouse with one daily bar of the stock `A` (`SBIN` in the server tests is created by passing the symbol to `make_main_for`).
    pub fn make_main(path: &Path, day: &str, close: f64) {
        make_main_for(path, "A", day, close);
    }

    pub fn make_main_for(path: &Path, symbol: &str, day: &str, close: f64) {
        let c = Connection::open(path).unwrap();
        c.execute_batch(&format!(
            "CREATE TABLE IF NOT EXISTS bars (symbol VARCHAR, day DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE); DELETE FROM bars; INSERT INTO bars VALUES ('{symbol}', '{day}', {close}, {close}, {close}, {close}, 10.0); CHECKPOINT;"
        ))
        .unwrap();
    }

    pub fn atomic_write(path: &Path, text: &str) {
        let tmp = path.with_file_name(format!("{}.tmp", path.file_name().unwrap().to_string_lossy()));
        std::fs::write(&tmp, text).unwrap();
        std::fs::rename(&tmp, path).unwrap();
    }

    pub fn gen_id(n: usize) -> String {
        format!("20261115T1015{:02}Z-{:06x}", n % 60, 0xa1b2c3 + n)
    }

    /// Publish generation `n` of `main` (a copy of it as it is now) and make it current; keep the newest two like the Python writer.
    pub fn publish(main: &Path, n: usize) -> String {
        let gen = gen_id(n);
        let data = main.with_file_name(data_name(main, &gen));
        std::fs::copy(main, &data).unwrap();
        let size = std::fs::metadata(&data).unwrap().len();
        let sha1 = sha1_of(&data).unwrap();
        let created = format!("2026-11-15T10:15:{:02}Z", n % 60);
        let manifest = json!({"schema": MANIFEST_SCHEMA, "generation_id": gen, "file": data_name(main, &gen), "size": size, "sha1": sha1, "created_at": created, "data_as_of": {"daily_bars": "2026-11-13", "index_bars": null}, "holder_job": "prices"});
        atomic_write(&main.with_file_name(manifest_name(main, &gen)), &manifest.to_string());
        let pointer = json!({"schema": POINTER_SCHEMA, "generation_id": gen, "file": data_name(main, &gen), "manifest": manifest_name(main, &gen), "size": size, "sha1": sha1});
        atomic_write(&pointer_path(main), &pointer.to_string());
        if n >= 2 {
            let old = gen_id(n - 2);
            let _ = std::fs::remove_file(main.with_file_name(data_name(main, &old)));
            let _ = std::fs::remove_file(main.with_file_name(manifest_name(main, &old)));
        }
        gen
    }

    pub fn ruin_main(main: &Path) {
        std::fs::write(main, b"this is not a database file at all, only text to make the real file unreadable").unwrap();
    }
}

#[cfg(test)]
mod tests {
    use super::testkit::*;
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::Arc;

    // ---- the real file ----
    #[test]
    fn a_readable_main_file_is_used_and_reported_as_main_with_an_observed_identity() {
        let dir = temp_dir("main");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        publish(&main, 0); // a snapshot exists, and is not used while the real file opens
        let o = open(&main, "t-main").ok().unwrap();
        assert_eq!(o.conn.query_row("SELECT close FROM bars", [], |r| r.get::<_, f64>(0)).unwrap(), 1.0);
        let p = &o.provenance;
        let meta = std::fs::metadata(&main).unwrap();
        assert_eq!((p.source, p.generation_basis, p.fallback_reason, p.validated), ("main", "observed", None, false));
        assert_eq!(p.generation_id, format!("main:{}:{}", meta.len(), mtime_nanos(&meta)));
        assert!(p.snapshot_created_at.is_none() && p.data_as_of.is_null());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn open_read_only_keeps_its_old_contract_a_connection_or_the_real_files_own_error() {
        let dir = temp_dir("compat");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        assert!(open_read_only(&main).is_ok());
        assert!(open_read_only(&dir.join("absent.duckdb")).is_err(), "no file and no snapshot is still an error");
        std::fs::remove_dir_all(&dir).ok();
    }

    // ---- fallback: a validated generation, with its provenance ----
    #[test]
    fn an_unreadable_main_with_a_validated_generation_answers_from_it_and_says_which_and_why() {
        let dir = temp_dir("fallback");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let gen = publish(&main, 3);
        atomic_write(&status_path(&main), &json!({"schema": "athena.snapshot-status/1", "last_attempt": {"outcome": "failed", "stage": "validate", "error": "x"}, "last_success": {"generation_id": gen}, "current_generation": gen}).to_string());
        ruin_main(&main);
        let o = open(&main, "t-fallback").ok().expect("a validated generation answers");
        assert_eq!(o.conn.query_row("SELECT close FROM bars", [], |r| r.get::<_, f64>(0)).unwrap(), 1.0);
        let p = &o.provenance;
        assert_eq!((p.source, p.generation_basis, p.validated, p.fallback_reason), ("snapshot", "manifest", true, Some("main_unreadable")));
        assert_eq!(p.generation_id, gen);
        assert_eq!(p.snapshot_created_at.as_deref(), Some("2026-11-15T10:15:03Z"));
        assert_eq!(p.data_as_of["daily_bars"], "2026-11-13"); // when the copy was taken (15th) is not what its data stops at (13th)
        assert_ne!(&p.snapshot_created_at.clone().unwrap()[..10], p.data_as_of["daily_bars"].as_str().unwrap());
        assert_eq!(p.snapshot_status["last_attempt"]["stage"], "validate"); // a newer attempt failed, and the answer says so
        assert!(p.main_error.is_some());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_missing_main_falls_back_with_its_own_reason() {
        let dir = temp_dir("missing");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        publish(&main, 1);
        std::fs::remove_file(&main).unwrap();
        assert_eq!(open(&main, "t-missing").ok().unwrap().provenance.fallback_reason, Some("main_missing"));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_reasons_a_main_file_failed_are_told_apart() {
        let p = Path::new("w.duckdb");
        assert_eq!(classify_open_error(false, p, "IO Error: Cannot open file"), "main_missing");
        assert_eq!(classify_open_error(false, Path::new("locks/w.duckdb"), "IO Error: Cannot open file"), "main_missing", "a missing file is missing even when its path names a lock");
        assert_eq!(classify_open_error(true, p, "IO Error: Could not set lock on file \"w.duckdb\": Conflicting lock is held in python.exe (PID 1)"), "locked_by_writer");
        assert_eq!(classify_open_error(true, p, "IO Error: The process cannot access the file because it is being used by another process."), "locked_by_writer");
        assert_eq!(classify_open_error(true, p, "Invalid Input Error: The file is not a valid DuckDB database file"), "main_unreadable");
    }

    // ---- T5-R5: a lock is recognised by the failure's wording, never by a substring of a path ----
    const PATH_NAMES: [&str; 5] = ["clock.duckdb", "block.duckdb", "lock.duckdb", "clockwork/w.duckdb", "unlocked/blocks/w.duckdb"];

    #[test]
    fn a_path_naming_a_lock_does_not_make_a_corrupt_or_unreadable_file_a_writer() {
        for name in PATH_NAMES {
            for text in [
                format!("IO Error: The file \"C:\\data\\{name}\" exists, but it is not a valid DuckDB database file!"),
                format!("IO Error: Cannot open file \"/data/{name}\": Access is denied."),
                format!("IO Error: Cannot read from file \"{name}\": the file is truncated"),
            ] {
                assert_eq!(classify_open_error(true, Path::new(name), &text), "main_unreadable", "{text}");
            }
        }
    }

    #[test]
    fn a_real_lock_message_is_a_writer_whatever_the_path_is_called() {
        for name in PATH_NAMES {
            for text in [
                format!("IO Error: Could not set lock on file \"/data/{name}\": Conflicting lock is held in /usr/bin/python3 (PID 4242) by user x. See also https://duckdb.org/docs/connect/concurrency"),
                format!("IO Error: Cannot open file \"C:\\data\\{name}\": The process cannot access the file because it is being used by another process.\n\nFile is already open in \nC:\\Python313\\python.exe (PID 42180)"),
            ] {
                assert_eq!(classify_open_error(true, Path::new(name), &text), "locked_by_writer", "{text}");
            }
        }
    }

    #[test]
    fn a_really_corrupt_file_in_a_clock_named_path_falls_back_as_unreadable_not_as_a_writers_lock() {
        for name in PATH_NAMES {
            let dir = temp_dir("corrupt-name");
            let main = dir.join(name);
            std::fs::create_dir_all(main.parent().unwrap()).unwrap();
            make_main(&main, "2026-11-13", 1.0);
            let gen = publish(&main, 0);
            ruin_main(&main); // DuckDB's own message names this path, with `clock`, `block` or `lock` in it
            let o = open(&main, "t-corrupt-name").ok().expect("a validated generation answers");
            assert_eq!((o.provenance.generation_id.as_str(), o.provenance.fallback_reason), (gen.as_str(), Some("main_unreadable")), "{name}: {:?}", o.provenance.main_error);
            std::fs::remove_dir_all(&dir).ok();
        }
    }

    // ---- no validated generation: an explicit failure, never a guess ----
    #[test]
    fn nothing_usable_is_an_explicit_failure_that_keeps_the_real_files_error() {
        let dir = temp_dir("none");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        ruin_main(&main);
        let f = open(&main, "t-none").err().expect("no snapshot published");
        assert_eq!((f.main_reason, f.snapshot.reason()), ("main_unreadable", "absent"));
        let v = f.to_value();
        assert_eq!(v["failure"]["snapshot_reason"], "absent");
        assert!(open_read_only(&main).is_err());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_single_file_copy_of_the_earlier_scheme_is_not_read_and_the_failure_says_so() {
        let dir = temp_dir("legacy");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        std::fs::copy(&main, snapshot_path(&main)).unwrap();
        ruin_main(&main);
        let f = open(&main, "t-legacy").err().unwrap();
        assert_eq!(f.snapshot.reason(), "absent");
        assert!(f.snapshot.detail().contains("no manifest"), "{}", f.snapshot.detail());
        std::fs::remove_dir_all(&dir).ok();
    }

    fn tamper(main: &Path, gen: &str, how: &str) {
        let (pointer, manifest, data) = (pointer_path(main), main.with_file_name(manifest_name(main, gen)), main.with_file_name(data_name(main, gen)));
        let edit = |p: &Path, f: &dyn Fn(&mut Value)| {
            let mut v: Value = serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap();
            f(&mut v);
            std::fs::write(p, v.to_string()).unwrap();
        };
        match how {
            "pointer_absent" => std::fs::remove_file(&pointer).unwrap(),
            "pointer_truncated" => std::fs::write(&pointer, &std::fs::read_to_string(&pointer).unwrap()[..20]).unwrap(),
            "pointer_wrong_schema" => edit(&pointer, &|v| v["schema"] = json!("something/9")),
            "pointer_path_traversal_id" => edit(&pointer, &|v| v["generation_id"] = json!("../../x")),
            "pointer_names_another_file" => edit(&pointer, &|v| v["file"] = json!("other.duckdb")),
            "pointer_size_not_a_number" => edit(&pointer, &|v| v["size"] = json!("12")),
            "manifest_missing" => std::fs::remove_file(&manifest).unwrap(),
            "manifest_truncated" => std::fs::write(&manifest, &std::fs::read_to_string(&manifest).unwrap()[..30]).unwrap(),
            "manifest_other_generation" => edit(&manifest, &|v| v["generation_id"] = json!("20200101T000000Z-aaaaaa")),
            "manifest_wrong_schema" => edit(&manifest, &|v| v["schema"] = json!("something/9")),
            "manifest_size_differs_from_pointer" => edit(&manifest, &|v| v["size"] = json!(v["size"].as_u64().unwrap() + 1)),
            "manifest_hash_differs_from_pointer" => edit(&manifest, &|v| v["sha1"] = json!("0".repeat(40))),
            "manifest_without_size" => edit(&manifest, &|v| {
                v.as_object_mut().unwrap().remove("size");
            }),
            "data_longer" => {
                use std::io::Write;
                std::fs::OpenOptions::new().append(true).open(&data).unwrap().write_all(b"x").unwrap()
            }
            "data_one_byte_changed_same_size" => {
                let mut raw = std::fs::read(&data).unwrap();
                let mid = raw.len() / 2;
                raw[mid] ^= 0xFF;
                std::fs::write(&data, raw).unwrap()
            }
            "data_missing" => std::fs::remove_file(&data).unwrap(),
            other => panic!("{other}"),
        }
    }

    #[test]
    fn every_disagreement_is_an_explicit_failure_never_a_generation() {
        let cases = [
            ("pointer_absent", "absent"), ("pointer_truncated", "pointer_invalid"), ("pointer_wrong_schema", "pointer_invalid"), ("pointer_path_traversal_id", "pointer_invalid"),
            ("pointer_names_another_file", "mismatch"), ("pointer_size_not_a_number", "pointer_invalid"), ("manifest_missing", "vanished"), ("manifest_truncated", "manifest_invalid"),
            ("manifest_other_generation", "mismatch"), ("manifest_wrong_schema", "manifest_invalid"), ("manifest_size_differs_from_pointer", "mismatch"),
            ("manifest_hash_differs_from_pointer", "mismatch"), ("manifest_without_size", "manifest_invalid"), ("data_longer", "size_mismatch"), ("data_one_byte_changed_same_size", "hash_mismatch"),
            ("data_missing", "vanished"),
        ];
        for (how, reason) in cases {
            let dir = temp_dir(how);
            let main = dir.join("w.duckdb");
            make_main(&main, "2026-11-13", 1.0);
            let gen = publish(&main, 0);
            tamper(&main, &gen, how);
            let f = resolve(&main).err().unwrap_or_else(|| panic!("{how} produced a generation"));
            assert_eq!(f.reason(), reason, "{how}: {}", f.detail());
            assert!(!f.detail().is_empty(), "{how} says what disagreed");
            // and through `open`: the same explicit reason, with the real file's own error kept
            ruin_main(&main);
            let o = open(&main, "t-tamper").err().unwrap_or_else(|| panic!("{how}: open returned data"));
            assert_eq!(o.snapshot.reason(), reason, "{how}");
            std::fs::remove_dir_all(&dir).ok();
        }
    }

    #[test]
    fn a_pointer_that_moves_after_the_read_gives_a_consistent_validated_pair() {
        let dir = temp_dir("move");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let first = publish(&main, 0);
        let m2 = main.clone();
        let hook = move || {
            make_main(&m2, "2026-11-14", 9.0);
            publish(&m2, 1);
        };
        let got = resolve_with(&main, true, Some(&hook)).ok().unwrap();
        assert_eq!(got.generation_id, first); // the generation the pointer named when it was read, whole
        assert_eq!(str_field(&got.manifest, "generation_id"), Some(got.generation_id.as_str()));
        assert_eq!(sha1_of(&got.path).unwrap(), str_field(&got.manifest, "sha1").unwrap());
        assert_ne!(resolve(&main).ok().unwrap().generation_id, first);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn cleanup_removing_the_generation_between_the_pointer_read_and_its_use_is_retried_through_a_fresh_pointer() {
        let dir = temp_dir("race");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let first = publish(&main, 0);
        let m2 = main.clone();
        let hook = move || {
            make_main(&m2, "2026-11-14", 2.0);
            publish(&m2, 1);
            make_main(&m2, "2026-11-15", 3.0);
            publish(&m2, 2); // the first generation is now removed by the keep-two cleanup
        };
        let got = resolve_with(&main, true, Some(&hook)).ok().unwrap();
        assert!(!main.with_file_name(data_name(&main, &first)).exists());
        assert_ne!(got.generation_id, first);
        assert_eq!(sha1_of(&got.path).unwrap(), str_field(&got.manifest, "sha1").unwrap());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_generation_removed_with_no_replacement_is_an_explicit_vanished_failure() {
        let dir = temp_dir("vanish");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let gen = publish(&main, 0);
        let m2 = main.clone();
        let hook = move || {
            let _ = std::fs::remove_file(m2.with_file_name(data_name(&m2, &gen)));
            let _ = std::fs::remove_file(m2.with_file_name(manifest_name(&m2, &gen)));
        };
        assert_eq!(resolve_with(&main, true, Some(&hook)).err().unwrap().reason(), "vanished");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_generation_deleted_between_validation_and_opening_is_retried_and_otherwise_explicit() {
        let dir = temp_dir("open-race");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let gen = publish(&main, 0);
        // deleted with a newer generation published: the retry opens the newer one
        let (m2, g2) = (main.clone(), gen.clone());
        let hook = move || {
            make_main(&m2, "2026-11-14", 2.0);
            publish(&m2, 1);
            let _ = std::fs::remove_file(m2.with_file_name(data_name(&m2, &g2)));
        };
        let (conn, v) = open_snapshot_with(&main, Some(&hook)).ok().unwrap();
        assert_ne!(v.generation_id, gen);
        assert_eq!(conn.query_row("SELECT close FROM bars", [], |r| r.get::<_, f64>(0)).unwrap(), 2.0);
        drop(conn);
        // deleted and nothing newer: an explicit failure
        let dir2 = temp_dir("open-race2");
        let main2 = dir2.join("w.duckdb");
        make_main(&main2, "2026-11-13", 1.0);
        let g = publish(&main2, 0);
        let (m3, g3) = (main2.clone(), g.clone());
        let hook2 = move || {
            let _ = std::fs::remove_file(m3.with_file_name(data_name(&m3, &g3)));
            let _ = std::fs::remove_file(m3.with_file_name(manifest_name(&m3, &g3)));
        };
        assert_eq!(open_snapshot_with(&main2, Some(&hook2)).err().unwrap().reason(), "vanished");
        std::fs::remove_dir_all(&dir).ok();
        std::fs::remove_dir_all(&dir2).ok();
    }

    #[test]
    fn a_changed_file_is_hashed_again_and_caught() {
        let dir = temp_dir("rehash");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let gen = publish(&main, 0);
        assert!(resolve(&main).is_ok() && resolve(&main).is_ok()); // verified once, remembered
        std::thread::sleep(Duration::from_millis(30));
        tamper(&main, &gen, "data_one_byte_changed_same_size"); // same size, new modification time
        assert_eq!(resolve(&main).err().unwrap().reason(), "hash_mismatch");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_writer_publishing_while_readers_read_never_yields_anything_but_a_validated_generation_or_an_explicit_failure() {
        let dir = temp_dir("stress");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 0.0);
        publish(&main, 0);
        let stop = Arc::new(AtomicBool::new(false));
        let problems = Arc::new(Mutex::new(Vec::<String>::new()));
        let seen = Arc::new(AtomicUsize::new(0));
        let readers: Vec<_> = (0..4)
            .map(|_| {
                let (main, stop, problems, seen) = (main.clone(), stop.clone(), problems.clone(), seen.clone());
                std::thread::spawn(move || {
                    while !stop.load(Ordering::Relaxed) {
                        match resolve(&main) {
                            Ok(v) => {
                                let consistent = v.path.file_name().unwrap().to_string_lossy() == data_name(&main, &v.generation_id) && str_field(&v.manifest, "generation_id") == Some(v.generation_id.as_str());
                                if !consistent {
                                    problems.lock().unwrap().push("manifest and file name disagree".into());
                                }
                                seen.fetch_add(1, Ordering::Relaxed);
                            }
                            Err(f) if matches!(f.reason(), "vanished" | "absent") => {}
                            Err(f) => problems.lock().unwrap().push(format!("unexpected failure {}: {}", f.reason(), f.detail())),
                        }
                    }
                })
            })
            .collect();
        for n in 1..=12 {
            make_main(&main, &format!("2026-11-{:02}", n + 1), n as f64);
            publish(&main, n);
            std::thread::sleep(Duration::from_millis(10));
        }
        stop.store(true, Ordering::Relaxed);
        for r in readers {
            r.join().unwrap();
        }
        assert_eq!(*problems.lock().unwrap(), Vec::<String>::new());
        assert!(seen.load(Ordering::Relaxed) > 0);
        std::fs::remove_dir_all(&dir).ok();
    }

    // ---- the contract with the Python writer, in both directions (run by hand with real fixture directories; ignored in an ordinary run) ----
    /// A generation published by `athena.warehouse.generations.publish` (Python) must validate and open here. `ATHENA_SNAPSHOT_PY_FIXTURE` names a directory holding `w.duckdb` and what Python published beside it.
    #[test]
    #[ignore = "cross-language: needs ATHENA_SNAPSHOT_PY_FIXTURE, a directory the Python writer published into"]
    fn a_generation_published_by_the_python_writer_validates_and_opens() {
        let dir = std::env::var("ATHENA_SNAPSHOT_PY_FIXTURE").expect("set ATHENA_SNAPSHOT_PY_FIXTURE");
        let main = PathBuf::from(dir).join("w.duckdb");
        let v = resolve(&main).unwrap_or_else(|f| panic!("{}: {}", f.reason(), f.detail()));
        assert_eq!(v.manifest["data_as_of"]["daily_bars"], "2026-11-13");
        assert_eq!(v.manifest["holder_job"], "prices");
        assert_eq!(v.manifest["created_at"], "2026-11-15T10:15:00Z");
        let (conn, opened) = open_snapshot(&main).ok().expect("opens");
        assert_eq!(opened.generation_id, v.generation_id);
        assert_eq!(conn.query_row("SELECT close FROM bars", [], |r| r.get::<_, f64>(0)).unwrap(), 1.0);
        let status: Value = serde_json::from_str(&std::fs::read_to_string(status_path(&main)).unwrap()).unwrap();
        assert_eq!(status["last_attempt"]["outcome"], "published"); // the separate status file the Python writer keeps
    }

    /// Publishes generations with the Rust test writer into `ATHENA_SNAPSHOT_RUST_OUT`, for the Python reader to validate (`tests/test_snapshot_crosslang.py`).
    #[test]
    #[ignore = "cross-language: needs ATHENA_SNAPSHOT_RUST_OUT, an empty directory to publish into"]
    fn publishes_generations_for_the_python_reader() {
        let dir = PathBuf::from(std::env::var("ATHENA_SNAPSHOT_RUST_OUT").expect("set ATHENA_SNAPSHOT_RUST_OUT"));
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        publish(&main, 0);
        make_main(&main, "2026-11-14", 2.0);
        publish(&main, 1);
    }

    #[test]
    fn a_generation_id_is_exactly_a_date_a_time_and_six_hex_digits() {
        assert!(generation_id_ok("20261115T101500Z-a1b2c3"));
        for bad in ["", "../../x", "20261115T101500Z-A1B2C3", "20261115T101500Z-a1b2c", "20261115T101500Z-a1b2c3d", "20261115 101500Z-a1b2c3", "2026111ST101500Z-a1b2c3"] {
            assert!(!generation_id_ok(bad), "{bad}");
        }
    }

    // ---- what each reader got, for the API and the page ----
    #[test]
    fn the_readers_last_answers_are_reported_and_different_generations_are_flagged_as_mixed() {
        // each assertion about the registry's arithmetic uses a registry of its own; the process-wide one is shared with every other test, so it is only checked for what this test itself opened (`srcs-…`)
        let reg = Registry::new();
        let dir = temp_dir("sources");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        let gen = publish(&main, 2);
        let o = open(&main, "srcs-bars").ok().unwrap(); // the real file
        reg.record_served_at(now_secs(), "srcs-bars", None, o.provenance.to_value());
        drop(o); // (Windows will not let the file be overwritten while a connection holds it)
        let s = reg.summarize(now_secs(), MIXED_WINDOW_SECS);
        assert_eq!(s["mixed_generations"], false);
        assert_eq!(s["readers"]["srcs-bars"]["source"], "main");
        ruin_main(&main);
        let o = open(&main, "srcs-calendar").ok().unwrap(); // a snapshot
        reg.record_served_at(now_secs(), "srcs-calendar", None, o.provenance.to_value());
        let s = reg.summarize(now_secs(), MIXED_WINDOW_SECS);
        assert_eq!(s["readers"]["srcs-calendar"]["generation_id"], gen);
        assert_eq!(s["readers"]["srcs-calendar"]["fallback_reason"], "main_unreadable");
        assert_eq!(s["mixed_generations"], true, "one reader on the file and one on a snapshot are on different generations");
        assert_eq!(s["recent_generations"].as_array().unwrap().len(), 2);
        // an answer older than the window no longer counts as recent
        let later = reg.summarize(now_secs() + MIXED_WINDOW_SECS + 5, MIXED_WINDOW_SECS);
        assert_eq!((later["mixed_generations"].clone(), later["readers"]["srcs-bars"]["recent"].clone()), (json!(false), json!(false)));
        // a failure is reported as a failure
        std::fs::remove_file(pointer_path(&main)).unwrap();
        assert!(open(&main, "srcs-search").is_err());
        let g = data_sources(); // the process-wide report: what `open` itself registered under its own names
        assert_eq!(g["readers"]["srcs-search"]["failure"]["snapshot_reason"], "absent");
        assert_eq!(g["readers"]["srcs-calendar"]["generation_id"], gen);
        assert!(g["readers"]["srcs-search"]["at"].is_u64() && g["window_seconds"] == MIXED_WINDOW_SECS && g["refresh_failures"].is_object());
        std::fs::remove_dir_all(&dir).ok();
    }

    // ---- T5-R3: the registry describes what was served, per subject, renewed when served again ----
    fn snap(gen: &str) -> Value {
        json!({"source": "snapshot", "generation_id": gen, "fallback_reason": "locked_by_writer", "data_as_of": {"daily_bars": "2026-11-13"}})
    }

    #[test]
    fn two_subjects_through_one_reader_keep_separate_entries_and_differing_generations_are_mixed() {
        let reg = Registry::new();
        reg.record_served_at(1_000, "bars", Some("SBIN:adjusted"), snap("20261115T101500Z-aaaaaa"));
        reg.record_served_at(1_000, "bars", Some("TCS:adjusted"), snap("20261115T102000Z-bbbbbb"));
        let s = reg.summarize(1_010, MIXED_WINDOW_SECS);
        assert_eq!(s["readers"]["bars:SBIN:adjusted"]["generation_id"], "20261115T101500Z-aaaaaa");
        assert_eq!(s["readers"]["bars:TCS:adjusted"]["generation_id"], "20261115T102000Z-bbbbbb");
        assert_eq!((s["readers"]["bars:SBIN:adjusted"]["reader"].clone(), s["readers"]["bars:SBIN:adjusted"]["subject"].clone()), (json!("bars"), json!("SBIN:adjusted")));
        assert_eq!(s["mixed_generations"], true);
        // the same subject served again replaces its own entry, it does not add one
        reg.record_served_at(1_020, "bars", Some("TCS:adjusted"), snap("20261115T101500Z-aaaaaa"));
        let s = reg.summarize(1_030, MIXED_WINDOW_SECS);
        assert_eq!((reg.len(), s["mixed_generations"].clone()), (2, json!(false)));
    }

    #[test]
    fn serving_an_answer_again_renews_it_so_a_cache_that_keeps_serving_stays_recent() {
        let reg = Registry::new();
        reg.record_served_at(1_000, "bars", Some("SBIN:adjusted"), snap("g1"));
        let stale = reg.summarize(1_000 + MIXED_WINDOW_SECS + 100, MIXED_WINDOW_SECS);
        assert_eq!(stale["readers"]["bars:SBIN:adjusted"]["recent"], false, "never served again: not recent");
        assert_eq!(stale["recent_generations"], json!([]));
        reg.record_served_at(1_000 + MIXED_WINDOW_SECS + 90, "bars", Some("SBIN:adjusted"), snap("g1")); // a cache hit serves it again
        let fresh = reg.summarize(1_000 + MIXED_WINDOW_SECS + 100, MIXED_WINDOW_SECS);
        assert_eq!((fresh["readers"]["bars:SBIN:adjusted"]["recent"].clone(), fresh["readers"]["bars:SBIN:adjusted"]["age_seconds"].clone(), fresh["recent_generations"].clone()), (json!(true), json!(10), json!(["g1"])));
    }

    #[test]
    fn a_refresh_failure_is_kept_apart_from_the_provenance_of_what_is_served() {
        let reg = Registry::new();
        reg.record_served_at(1_000, "search", None, snap("g1"));
        reg.record_refresh_failure_at(1_005, "search", json!({"failure": {"main_reason": "main_unreadable", "snapshot_reason": "absent"}}));
        let s = reg.summarize(1_010, MIXED_WINDOW_SECS);
        assert_eq!(s["readers"]["search"]["generation_id"], "g1", "what was served is still what was served");
        assert!(s["readers"]["search"].get("failure").is_none());
        assert_eq!(s["refresh_failures"]["search"]["failure"]["snapshot_reason"], "absent");
        assert_eq!(s["refresh_failures"]["search"]["at"], 1_005);
        reg.clear_refresh_failure("search"); // a later refresh succeeded
        assert_eq!(reg.summarize(1_020, MIXED_WINDOW_SECS)["refresh_failures"], json!({}));
    }

    // ---- T5-F2: a refresh failure has an age and is recent only by the same clock and window as a served answer ----
    fn failure() -> Value {
        json!({"failure": {"main_reason": "main_unreadable", "snapshot_reason": "absent"}})
    }

    #[test]
    fn a_refresh_failure_has_age_and_recent_by_the_same_clock_and_window_as_a_served_entry_including_the_boundary() {
        let reg = Registry::new();
        reg.record_served_at(1_000, "search", None, snap("g1"));
        reg.record_refresh_failure_at(1_000, "search", failure());
        let on_boundary = reg.summarize(1_000 + MIXED_WINDOW_SECS, MIXED_WINDOW_SECS);
        let f = &on_boundary["refresh_failures"]["search"];
        assert_eq!((f["age_seconds"].clone(), f["recent"].clone(), f["at"].clone(), f["reader"].clone()), (json!(MIXED_WINDOW_SECS), json!(true), json!(1_000), json!("search")), "exactly at the window is still recent: {f}");
        assert_eq!(on_boundary["readers"]["search"]["recent"], true, "the served entry follows the same rule");
        let past = reg.summarize(1_000 + MIXED_WINDOW_SECS + 1, MIXED_WINDOW_SECS);
        assert_eq!((past["refresh_failures"]["search"]["recent"].clone(), past["refresh_failures"]["search"]["age_seconds"].clone()), (json!(false), json!(MIXED_WINDOW_SECS + 1)));
        assert_eq!(past["readers"]["search"]["recent"], false);
        assert_eq!(past["refresh_failures"]["search"]["failure"]["snapshot_reason"], "absent", "an old failure stays available as history");
    }

    #[test]
    fn a_failure_that_happens_again_is_current_again_and_one_that_does_not_stays_history() {
        let reg = Registry::new();
        reg.record_refresh_failure_at(1_000, "search", failure());
        let old = reg.summarize(1_000 + MIXED_WINDOW_SECS + 50, MIXED_WINDOW_SECS);
        assert_eq!(old["refresh_failures"]["search"]["recent"], false);
        reg.record_refresh_failure_at(1_000 + MIXED_WINDOW_SECS + 40, "search", failure()); // the next attempt failed too
        let again = reg.summarize(1_000 + MIXED_WINDOW_SECS + 50, MIXED_WINDOW_SECS);
        assert_eq!((again["refresh_failures"]["search"]["recent"].clone(), again["refresh_failures"]["search"]["age_seconds"].clone()), (json!(true), json!(10)));
        reg.clear_refresh_failure("search");
        assert_eq!(reg.summarize(1_000 + MIXED_WINDOW_SECS + 60, MIXED_WINDOW_SECS)["refresh_failures"], json!({}), "a successful refresh removes it");
    }

    #[test]
    fn the_registry_is_bounded_and_drops_the_entry_served_longest_ago() {
        let reg = Registry::new();
        for i in 0..(MAX_ENTRIES + 10) {
            reg.record_served_at(1_000 + i as u64, "bars", Some(&format!("S{i}:adjusted")), snap("g"));
        }
        assert_eq!(reg.len(), MAX_ENTRIES);
        let s = reg.summarize(2_000, MIXED_WINDOW_SECS);
        assert!(s["readers"].get("bars:S0:adjusted").is_none() && s["readers"].get(format!("bars:S{}:adjusted", MAX_ENTRIES + 9)).is_some());
        // entries made in the same second never evict the one just written
        let same = Registry::new();
        for i in 0..(MAX_ENTRIES + 5) {
            same.record_served_at(500, "bars", Some(&format!("S{i:04}")), snap("g"));
        }
        assert!(same.summarize(500, MIXED_WINDOW_SECS)["readers"].get(format!("bars:S{:04}", MAX_ENTRIES + 4)).is_some());
    }

    #[test]
    fn opening_for_a_build_records_nothing_and_opening_for_an_answer_records_it() {
        let dir = temp_dir("unrecorded");
        let main = dir.join("w.duckdb");
        make_main(&main, "2026-11-13", 1.0);
        open_unrecorded(&main).ok().unwrap();
        assert!(data_sources()["readers"].get("unrecorded-quiet").is_none());
        open(&main, "unrecorded-loud").ok().unwrap();
        assert_eq!(data_sources()["readers"]["unrecorded-loud"]["source"], "main");
        std::fs::remove_dir_all(&dir).ok();
    }

    // ---- T5-R4: the payloads the shell reads are produced here, and the web tests read the same file ----
    const NOW: u64 = 1_794_600_000;

    /// The registry payload with everything that depends on the machine or the moment replaced by a fixed word, so the same inputs always give the same text.
    fn scrub(mut v: Value) -> Value {
        match &mut v {
            Value::Object(o) => {
                for (k, x) in o.iter_mut() {
                    match k.as_str() {
                        "source_path" | "main_error" if x.is_string() => *x = json!("<scrubbed>"),
                        "generation_id" if x.as_str().is_some_and(|s| s.starts_with("main:")) => *x = json!("main:<observed>"),
                        _ => *x = scrub(x.take()),
                    }
                }
            }
            Value::Array(a) => {
                for x in a.iter_mut() {
                    *x = scrub(x.take());
                }
            }
            Value::String(s) if s.starts_with("main:") => *s = "main:<observed>".to_string(),
            _ => {}
        }
        v
    }

    fn set_dates(main: &Path, gen: &str, daily: &str, index: &str) {
        let p = main.with_file_name(manifest_name(main, gen));
        let mut m: Value = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        m["data_as_of"] = json!({"daily_bars": daily, "index_bars": index});
        atomic_write(&p, &m.to_string());
    }

    fn write_status(main: &Path, last_attempt: Value, gen: Option<&str>) {
        atomic_write(&status_path(main), &json!({"schema": "athena.snapshot-status/1", "updated_at": "2026-11-15T10:30:00Z", "last_attempt": last_attempt, "last_success": gen.map(|g| json!({"at": "2026-11-15T10:15:00Z", "generation_id": g})), "current_generation": gen, "cleanup": {"removed": [], "deferred": []}}).to_string());
    }

    fn served(reg: &Registry, at: u64, reader: &str, subject: Option<&str>, opened: Result<Opened, OpenFailure>) {
        let value = match &opened {
            Ok(o) => o.provenance.to_value(),
            Err(f) => f.to_value(),
        };
        reg.record_served_at(at, reader, subject, value);
    }

    /// The `/api/data-source` payload of each situation the shell must tell apart, built from the same code the server runs: real files, real generations, real failures.
    fn the_cases() -> Value {
        let mut cases = serde_json::Map::new();
        let failed = json!({"at": "2026-11-15T10:30:00Z", "outcome": "failed", "stage": "validate", "error": "OSError: copy would not open", "generation_attempted": "20261115T103000Z-c0ffee", "current_unusable": "hash_mismatch"});
        let case = |reg: &Registry, publication: Value| -> Value {
            let mut v = reg.summarize(NOW, MIXED_WINDOW_SECS);
            v["snapshot_publication"] = publication;
            scrub(v)
        };
        let fresh = |tag: &str| {
            let dir = temp_dir(tag);
            let main = dir.join("w.duckdb");
            make_main(&main, "2026-11-13", 1.0);
            (dir, main)
        };

        // the real file answers everywhere
        let (dir, main) = fresh("fx-main");
        let reg = Registry::new();
        served(&reg, NOW - 5, "calendar", None, open_unrecorded(&main));
        served(&reg, NOW - 3, crate::fundamentals::PRICE_READER, None, open_unrecorded(&main));
        cases.insert("main_only".into(), case(&reg, Value::Null));
        // a build holds the file: a snapshot answers, with two datasets that stop on different days; the daily answer states its own date
        let gen = publish(&main, 3);
        set_dates(&main, &gen, "2026-11-13", "2026-11-12");
        write_status(&main, json!({"at": "2026-11-15T10:15:00Z", "outcome": "published", "generation_id": gen}), Some(&gen));
        let v = resolve(&main).ok().unwrap();
        let locked = |reason: &'static str| Provenance::snapshot(&main, &v, reason, "IO Error: Could not set lock on file \"w.duckdb\": Conflicting lock is held in python.exe (PID 1)".into()).to_value();
        let reg = Registry::new();
        reg.record_served_at(NOW - 5, "calendar", None, locked("locked_by_writer"));
        reg.record_served_at(NOW - 4, "bars", Some("SBIN:adjusted"), crate::server::bars_provenance(Some(locked("locked_by_writer")), Some(1_794_528_000)));
        cases.insert("locked_snapshot".into(), case(&reg, publication_status(&main)));
        // a snapshot answers while a newer copy failed
        let newer_failed = json!({"at": "2026-11-15T10:30:00Z", "outcome": "failed", "stage": "validate", "error": "OSError: copy would not open", "generation_attempted": "20261115T103000Z-c0ffee", "current_unusable": null});
        write_status(&main, newer_failed, Some(&gen)); // the generation in place is still usable; a newer one could not be made
        let reg = Registry::new();
        reg.record_served_at(NOW - 5, "calendar", None, locked("locked_by_writer"));
        cases.insert("failed_replacement_snapshot".into(), case(&reg, publication_status(&main)));
        // the real file answers, and the last copy failed and left the generation in place damaged
        write_status(&main, failed.clone(), None);
        let reg = Registry::new();
        served(&reg, NOW - 5, "calendar", None, open_unrecorded(&main));
        cases.insert("failed_publication_main".into(), case(&reg, publication_status(&main)));
        // the real file cannot be read / is missing: a snapshot answers, with the reason the real file failed
        write_status(&main, json!({"at": "2026-11-15T10:15:00Z", "outcome": "published", "generation_id": gen}), Some(&gen));
        ruin_main(&main);
        let reg = Registry::new();
        served(&reg, NOW - 5, "calendar", None, open_unrecorded(&main));
        cases.insert("main_unreadable_snapshot".into(), case(&reg, publication_status(&main)));
        std::fs::remove_file(&main).unwrap();
        let reg = Registry::new();
        served(&reg, NOW - 5, "calendar", None, open_unrecorded(&main));
        cases.insert("main_missing_snapshot".into(), case(&reg, publication_status(&main)));
        // an entry that has not been served for longer than the window
        let reg = Registry::new();
        reg.record_served_at(NOW - MIXED_WINDOW_SECS - 100, "calendar", None, locked("locked_by_writer"));
        cases.insert("stale_entry".into(), case(&reg, Value::Null));
        // nothing could be opened, and why
        std::fs::remove_file(pointer_path(&main)).unwrap();
        make_main(&main, "2026-11-13", 1.0);
        ruin_main(&main);
        let reg = Registry::new();
        served(&reg, NOW - 5, "search", None, open_unrecorded(&main));
        cases.insert("no_data".into(), case(&reg, Value::Null));
        std::fs::remove_dir_all(&dir).ok();

        // two answers from different generations
        let (dir, main) = fresh("fx-mixed");
        let g1 = publish(&main, 1);
        let v1 = resolve(&main).ok().unwrap();
        let g2 = publish(&main, 2);
        let v2 = resolve(&main).ok().unwrap();
        assert_ne!(g1, g2);
        let reg = Registry::new();
        reg.record_served_at(NOW - 6, "bars", Some("ZMIXA:adjusted"), crate::server::bars_provenance(Some(Provenance::snapshot(&main, &v1, "locked_by_writer", "locked".into()).to_value()), Some(1_794_528_000)));
        reg.record_served_at(NOW - 2, "bars", Some("ZMIXB:adjusted"), crate::server::bars_provenance(Some(Provenance::snapshot(&main, &v2, "locked_by_writer", "locked".into()).to_value()), Some(1_794_528_000)));
        cases.insert("mixed_generations".into(), case(&reg, Value::Null));
        // a refresh failed while the index built from the real file is still being served
        let failure = open_unrecorded(&dir.join("absent.duckdb")).err().unwrap().to_value(); // a real failure value: the refresh of the index could not open the file
        let reg = Registry::new();
        reg.record_served_at(NOW - 5, "search", None, Provenance::main(&main).to_value()); // the index built from the real file is still the one being served
        reg.record_refresh_failure_at(NOW - 20, "search", failure.clone());
        cases.insert("refresh_failure_retained".into(), case(&reg, Value::Null));
        // T5-F2: when a failed refresh is current, and when it is only history. Each is a real failure value and the registry's own age/recent arithmetic at NOW.
        let w = MIXED_WINDOW_SECS;
        let main_served = Provenance::main(&main).to_value();
        let timing = |failure_at: u64, served_at: Option<u64>, served_reader: &str| -> Value {
            let reg = Registry::new();
            if let Some(at) = served_at {
                reg.record_served_at(at, served_reader, None, main_served.clone());
            }
            reg.record_refresh_failure_at(failure_at, "search", failure.clone());
            case(&reg, Value::Null)
        };
        cases.insert("refresh_failure_old_no_recent_serve".into(), timing(NOW - w - 100, None, "search"));
        cases.insert("refresh_failure_old_renewed_serving".into(), timing(NOW - w - 100, Some(NOW - 3), "search")); // the cache kept being served, and nothing has failed since
        cases.insert("refresh_failure_at_window_boundary".into(), timing(NOW - w, Some(NOW - 2), "search"));
        cases.insert("refresh_failure_past_window_boundary".into(), timing(NOW - w - 1, Some(NOW - 2), "search"));
        cases.insert("refresh_failure_current_nothing_served_for_it".into(), timing(NOW - 10, Some(NOW - 2), "calendar")); // a failed refresh, and only another reader has been served
        std::fs::remove_dir_all(&dir).ok();

        // T5-F1: the Fundamentals page's cached closes, from the real cache code (`fundamentals.rs`); only the clock is replaced, so the cases are deterministic
        let put_screen = |path: &Path, close: f64| {
            Connection::open(path).unwrap().execute_batch(&format!("DROP TABLE IF EXISTS screen; CREATE TABLE screen (symbol VARCHAR, close DOUBLE, data_as_of DATE); INSERT INTO screen VALUES ('TCS', {close}, DATE '2026-10-16'); CHECKPOINT;")).unwrap();
        };
        let jobs_on = |main: &Path, reader: &str| crate::fundamentals::FundamentalsJobs::new(vec![], vec![], main.to_path_buf(), PathBuf::from("x")).with_price_reader(reader);
        let replay = |reg: &Registry, reader: &str, served_at: u64, failure_at: Option<u64>, cached_age: Option<u64>| {
            let (served, failed) = global().raw(reader);
            if let Some(mut s) = served {
                if let (Some(age), Some(c)) = (cached_age, s.get_mut("cache")) {
                    c["age_seconds"] = json!(age);
                }
                reg.record_served_at(served_at, reader, None, s);
            }
            if let (Some(f), Some(at)) = (failed, failure_at) {
                reg.record_refresh_failure_at(at, reader, f);
            }
        };
        // read from the real file: the data is as the cache was read
        let (dir, main) = fresh("fx-price");
        put_screen(&main, 3200.5);
        let jobs = jobs_on(&main, "fx-price-main");
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let reg = Registry::new();
        replay(&reg, "fx-price-main", NOW - 4, None, None);
        cases.insert("price_cache_main".into(), case(&reg, Value::Null));
        // the same cache, its refresh fails and the held closes are still returned
        ruin_main(&main);
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3200.5));
        let reg = Registry::new();
        replay(&reg, "fx-price-main", NOW - 3, Some(NOW - 3), Some(30));
        cases.insert("price_cache_retained_after_refresh_failure".into(), case(&reg, Value::Null));
        // …and the same cache next to another reader that is answering from a snapshot: two generations
        std::fs::remove_file(&main).unwrap();
        put_screen(&main, 3200.5);
        let gen = publish(&main, 3);
        let jobs = jobs_on(&main, "fx-price-mixed");
        jobs.price("TCS").unwrap();
        ruin_main(&main);
        let screen = open_unrecorded(&main).ok().expect("a snapshot answers the other reader");
        let reg = Registry::new();
        replay(&reg, "fx-price-mixed", NOW - 4, None, Some(12));
        reg.record_served_at(NOW - 2, "analysis", None, screen.provenance.to_value());
        assert_eq!(screen.provenance.generation_id, gen);
        cases.insert("price_cache_and_analysis_on_different_generations".into(), case(&reg, Value::Null));
        drop(screen);
        // nothing held and nothing readable: no price, and why
        let (dir2, none) = fresh("fx-price-none");
        put_screen(&none, 1.0);
        ruin_main(&none);
        let jobs = jobs_on(&none, "fx-price-none");
        assert_eq!(jobs.price("TCS"), None);
        let reg = Registry::new();
        replay(&reg, "fx-price-none", NOW - 3, Some(NOW - 3), None);
        cases.insert("price_cache_no_data".into(), case(&reg, Value::Null));
        // recovered: a later read succeeded, the failure is gone and the new data is served
        let (dir3, back) = fresh("fx-price-back");
        put_screen(&back, 3200.5);
        let jobs = jobs_on(&back, "fx-price-back");
        jobs.price("TCS").unwrap();
        ruin_main(&back);
        jobs.price("TCS").unwrap();
        std::fs::remove_file(&back).unwrap();
        put_screen(&back, 3300.0);
        assert_eq!(jobs.price("TCS").map(|p| p.0), Some(3300.0));
        let reg = Registry::new();
        replay(&reg, "fx-price-back", NOW - 2, Some(NOW - 2), None); // (no failure is left to replay)
        assert!(global().raw("fx-price-back").1.is_none(), "a good read cleared it");
        cases.insert("price_cache_recovered".into(), case(&reg, Value::Null));
        for d in [dir, dir2, dir3] {
            std::fs::remove_dir_all(&d).ok();
        }
        Value::Object(cases)
    }

    #[test]
    fn the_registry_payloads_match_the_ui_fixtures() {
        let got = the_cases();
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("web/test/fixtures/data-source.cases.json");
        if std::env::var("ATHENA_UPDATE_FIXTURES").as_deref() == Ok("1") {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, serde_json::to_string_pretty(&got).unwrap() + "\n").unwrap();
            return;
        }
        let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e} (run with ATHENA_UPDATE_FIXTURES=1 to write it)", path.display()));
        let want: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(got, want, "the registry's payloads no longer match terminal/web/test/fixtures/data-source.cases.json: review the change, then regenerate with ATHENA_UPDATE_FIXTURES=1");
    }
}
