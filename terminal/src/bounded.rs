//! Bounded queued work for the Python workers (audit T6).
//!
//! Before this, a request waited for the worker's mutex with no limit and only then began its own timeout, so the timeout did not bound the time a person waited; the queue had no size; the startup of a
//! worker had a limit of its own on top; and a timed-out worker was dropped by `kill_on_drop` without anyone confirming that the process had exited. Here:
//!
//! - **A bounded queue.** A submission with no free worker and no free place is refused at once (`Refusal::QueueFull`); nothing is queued and nothing already accepted is touched. Each accepted job has an ID
//!   and a *kind* (a fixed name such as `view` or `chart_bars`, never text a person or a page supplied) so that contention can be attributed.
//! - **One deadline per job**, measured from its submission, covering the wait in the queue, the child's start and the execution: the time a job has to run is what remains of it. A job whose deadline passes
//!   while it waits is `expired` (`deadline_before_start`), is never given a worker and frees its place.
//! - **Stop conditions win.** A job that has been cancelled, or whose deadline has passed, is never given a child and nothing is sent to one: both are looked at before the child is started, before the request is
//!   sent, and (first, in the `select!`) against a child that is ready at the same instant.
//! - **Queued cancellation** removes the job and touches nothing else (the others keep their order). Dropping a `Ticket` is a cancellation.
//! - **Running cancellation and timeout** end the child with terminate, wait, kill, wait (`end`), and the worker is released only when the exit is **confirmed**. Whoever is waiting is told at the moment of the
//!   deadline or the cancellation, not after the child has been dealt with; a failure of a request that was already sent (`no_answer`) is told only after the child is known to be gone (`Outcome::Failed`) or
//!   not to be (`Outcome::Unconfirmed`), so a caller that may run the same work elsewhere is never running it beside a child that is still going.
//! - **Unconfirmed termination** keeps the worker (the job is `cancel_unconfirmed`): no new job takes it, and when every worker is held new submissions are refused (`Refusal::Held`). The way out is explicit: the
//!   pool re-tries the termination every `Limits::recheck`, and a person can ask for another attempt or say the process is gone (`Pool::resolve`, `POST /api/work/resolve`), as the supervisor's lock is released
//!   only on confirmation or by a person (T4). A person gives their name and a reason, and both are recorded with the job. Every held child is identified by its process ID and launch number in the status.
//! - **Restart.** When the terminal itself ends (a crash, a kill) a held child may still be running, and nothing in this process can end it. With a journal (`Pool::with_journal`) the held children are written to a
//!   small file (pool, slot, job, kind, pid, launch number, time: never what was asked); the next run reads it, and each such worker starts **held** (`origin: previous_run`) so a new job cannot be put beside a
//!   process that may still be running. There is no handle on that process, so the terminal does not try to end it: only a person can free it, with `confirmed_gone`, after checking the pid. A journal that cannot
//!   be read or written is reported in the status and never stops the pool.
//! - **Shutdown** (`Pool::shutdown`) closes the pool, cancels what waits and what runs, ends every child (terminate, wait, kill, wait), stops the workers and reports what could not be confirmed gone. Dropping the pool
//!   and then the last ticket also ends an idle worker's child: a worker is never left parked on a pool nobody can reach.
//! - **Per-job queue wait and running time** are recorded (`GET /api/work`), per job and per kind: the numbers that decide whether backtests should be split from model work, which is deliberately not built.
//!
//! What this does **not** do: a child whose parent dies is not ended by anything here (`kill_on_drop` acts only while this process is alive to drop the handle). The journal and the explicit resolution are the
//! answer to that, not a cleanup.
//!
//! The decisions are in `Core`, a synchronous state machine with explicit instants (no clock of its own); `Pool` runs one task per worker around it; the child is behind `Launcher`/`Live`, so the tests use fake
//! children and a paused clock. The limits are not calibrated on real load.
use std::collections::{BTreeMap, VecDeque};
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{oneshot, Notify, Semaphore};
use tokio::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Done,
    Failed,
    Cancelled,
    Expired,
    CancelUnconfirmed,
}

impl JobState {
    fn terminal(self) -> bool {
        !matches!(self, JobState::Queued | JobState::Running | JobState::CancelUnconfirmed)
    }
}

/// One person's override of a held worker, as it is recorded with the job.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct OverrideRecord {
    /// `confirmed_gone` or `retry`.
    pub action: &'static str,
    pub operator: String,
    pub reason: String,
    /// When, since the pool was made.
    pub at_ms: u64,
    pub at_unix_ms: u64,
}

/// What the pool remembers of one job (the last `Limits::history` finished ones, and every live one).
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct JobRecord {
    pub id: u64,
    /// What sort of work it was: a fixed name chosen by the code that submitted it, never what was asked.
    pub kind: &'static str,
    pub state: JobState,
    pub reason: Option<String>,
    /// How a held worker was freed: `confirmed_exit_later`, `confirmed_after_retry`, `confirmed_at_shutdown` or `resolved_by_person`.
    pub resolution: Option<String>,
    /// The total time the job was given, from its submission.
    pub deadline_ms: u64,
    /// When it was submitted, since the pool was made.
    pub submitted_ms: u64,
    /// How long it waited for a worker (set when it gets one or expires).
    pub queue_wait_ms: Option<u64>,
    /// How long it held a worker.
    pub run_ms: Option<u64>,
    /// The operating system's number for the child that ran it (when it ran on one).
    pub pid: Option<u32>,
    /// Which launch of a child it was (counts from 1 in each pool).
    pub child: Option<u64>,
    /// Every person's override of its held worker.
    pub overrides: Vec<OverrideRecord>,
    /// Its caller went away while it ran: it was let finish and its result was discarded.
    pub abandoned: bool,
}

#[derive(Clone, Copy, Debug)]
pub struct Limits {
    /// How many children serve jobs side by side.
    pub workers: usize,
    /// How many jobs may wait for a worker (the one that is running does not count).
    pub capacity: usize,
    /// The total time a job is given when its caller does not say.
    pub deadline: Duration,
    /// The longest a child may take to say it is ready (inside the job's deadline).
    pub start_limit: Duration,
    /// Each of the two waits of the termination routine.
    pub grace: Duration,
    /// How often a held child's termination is tried again.
    pub recheck: Duration,
    /// How many finished jobs the status keeps.
    pub history: usize,
}

impl Default for Limits {
    /// The analysis worker's: one child (the conservative count the terminal always had), four places, 300 s in total, 180 s to start.
    fn default() -> Self {
        Limits { workers: 1, capacity: 4, deadline: Duration::from_secs(300), start_limit: Duration::from_secs(180), grace: Duration::from_secs(5), recheck: Duration::from_secs(15), history: 200 }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// No worker is free and every place is taken.
    QueueFull { capacity: usize },
    /// Every worker is held by a child that could not be confirmed gone.
    Held { job: u64 },
    /// The pool has been shut down (or is being): nothing new is accepted.
    Closed,
    /// The recovery state (the journal of held children) could not be read, or a held child could not be written to it: nothing is admitted until a person acknowledges the one or the journal is written (`retry_journal`).
    Unrecovered,
}

/// What the caller of a job is told. For a deadline or a cancellation it is told when it happens; the child is dealt with after.
#[derive(Clone, Debug, PartialEq)]
pub enum Outcome {
    /// The child answered (whatever the answer says: the protocol's own `ok` is the caller's to read).
    Reply(Value),
    /// It could not be done and nothing of it is left running: `start_failed`, `no_answer`.
    Failed(&'static str),
    /// The deadline passed: `deadline_before_start`, `deadline_during_start`, `deadline_exceeded`.
    Expired(&'static str),
    Cancelled,
    /// The job failed and its child could not be confirmed gone: whatever it was doing may still be running, so a caller must not start the same work again elsewhere.
    Unconfirmed(&'static str),
}

/// How a person frees a held worker.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Resolution {
    /// A person has checked that the process is gone: the pool stops waiting for confirmation and the worker is freed.
    ConfirmedGone,
    /// Try to end the process again now.
    Retry,
    /// The recovery state could not be read; a person accepts that and the pool starts afresh (the unreadable file is kept beside it).
    AcknowledgeRecovery,
    /// The journal could not be written while something is held (so admission is blocked): write it again now. Admits nothing.
    RetryJournal,
}

impl Resolution {
    fn name(self) -> &'static str {
        match self {
            Resolution::ConfirmedGone => "confirmed_gone",
            Resolution::Retry => "retry",
            Resolution::AcknowledgeRecovery => "acknowledge_recovery",
            Resolution::RetryJournal => "retry_journal",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResolveError {
    NotRetained,
    /// The worker is held from an earlier run of the terminal: there is no process handle to try again with, only a person's word that the process is gone.
    NoHandle,
    /// Nothing is blocked on the recovery state.
    NotBlocked,
    /// The unreadable recovery file could not be put aside, so nothing was changed.
    JournalError,
}

/// Who changed what by hand, and why. Both are free text from a person: trimmed, bounded, and with no control characters.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OverrideNote {
    pub operator: String,
    pub reason: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NoteError {
    Operator,
    Reason,
}

const MAX_OPERATOR: usize = 64;
const MAX_REASON: usize = 240;

impl OverrideNote {
    pub fn new(operator: &str, reason: &str) -> Result<OverrideNote, NoteError> {
        let fit = |text: &str, max: usize| {
            let t = text.trim();
            (!t.is_empty() && t.chars().count() <= max && !t.chars().any(char::is_control)).then(|| t.to_string())
        };
        Ok(OverrideNote { operator: fit(operator, MAX_OPERATOR).ok_or(NoteError::Operator)?, reason: fit(reason, MAX_REASON).ok_or(NoteError::Reason)? })
    }
}

/// What `Pool::shutdown` found.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ShutdownReport {
    /// Children that were ended and confirmed gone while the pool was being shut down.
    pub ended: usize,
    /// Children that could not be confirmed gone (they stay in the journal, when there is one).
    pub unconfirmed: usize,
}

/// What a person's request about a held entry did.
#[derive(Debug, PartialEq, Eq)]
pub enum Resolved {
    /// The worker's hold task has been asked (wake this worker).
    Wake(usize),
    /// The entry was freed at once; these workers were given jobs (wake them).
    Done(Vec<usize>),
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SlotView {
    Idle,
    Busy(u64),
    Retained(u64),
}

/// A job's stop signal: set when it is cancelled (or the pool is shut down). A flag and a wake-up, so it can be looked at before anything is started and awaited while something runs.
pub struct Stop {
    set: AtomicBool,
    /// Its caller went away. Unlike a cancellation this only matters before the job has started: a job that is running is read-only work and is let finish (its result is discarded).
    abandoned: AtomicBool,
    wake: Notify,
}

impl Stop {
    fn new() -> Arc<Stop> {
        Arc::new(Stop { set: AtomicBool::new(false), abandoned: AtomicBool::new(false), wake: Notify::new() })
    }
    fn trip(&self) {
        self.set.store(true, Ordering::SeqCst);
        self.wake.notify_one();
    }
    pub fn is_set(&self) -> bool {
        self.set.load(Ordering::SeqCst)
    }
    fn abandon(&self) {
        self.abandoned.store(true, Ordering::SeqCst);
    }
    fn is_abandoned(&self) -> bool {
        self.abandoned.load(Ordering::SeqCst)
    }
    async fn tripped(&self) {
        loop {
            if self.is_set() {
                return;
            }
            self.wake.notified().await;
        }
    }
}

pub enum Cancelled {
    /// It was waiting: it is removed and its caller told.
    Queued,
    /// It is running (or about to): its stop signal is set; the worker ends it.
    Running,
    Unknown,
}

/// A job a worker has been given.
pub struct Assigned {
    pub id: u64,
    pub kind: &'static str,
    pub payload: Value,
    pub submitted: Duration,
    pub deadline: Duration,
    pub tx: Option<oneshot::Sender<Outcome>>,
    pub stop: Arc<Stop>,
}

impl Assigned {
    /// What is left of the job's deadline at `now`: the wait in the queue is already spent.
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn remaining(&self, now: Duration) -> Duration {
        (self.submitted + self.deadline).saturating_sub(now)
    }
}

/// How a job ended, as the worker reports it.
#[derive(Clone, Copy, Debug)]
pub struct Finish {
    /// What the job is, once its child is confirmed gone.
    pub state: JobState,
    pub reason: &'static str,
    /// The child could not be confirmed gone: the job is `cancel_unconfirmed` and the worker is held.
    pub retain: bool,
}

/// Which child: its launch number in the pool and the operating system's number for it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ChildInfo {
    pub seq: u64,
    pub pid: Option<u32>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Origin {
    ThisRun,
    /// Held by an earlier run of the terminal: there is no handle on the process.
    PreviousRun,
    /// A child of this run whose handle is gone (an idle child that could not be confirmed gone when its worker stopped).
    NoHandle,
}

impl Origin {
    /// There is no process handle to try again with: only a person can free it.
    fn handleless(self) -> bool {
        !matches!(self, Origin::ThisRun)
    }
}

/// A held child as it is written to the journal.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HeldEntry {
    pub slot: Option<usize>,
    pub job: u64,
    pub kind: String,
    pub pid: Option<u32>,
    pub child: Option<u64>,
    pub since_unix_ms: u64,
}

struct Entry {
    id: u64,
    kind: &'static str,
    payload: Value,
    submitted: Duration,
    deadline: Duration,
    tx: Option<oneshot::Sender<Outcome>>,
}

struct Held {
    job: u64,
    kind: &'static str,
    since: Duration,
    since_unix_ms: u64,
    attempts: u32,
    intended: JobState,
    reason: &'static str,
    requested: Option<Resolution>,
    child: Option<ChildInfo>,
    origin: Origin,
}

enum Slot {
    Idle,
    /// A job has been given to this worker, which has not yet picked it up.
    Assigned(Assigned),
    Busy { id: u64, kind: &'static str, child: Option<ChildInfo>, stop: Arc<Stop> },
    Retained(Held),
}

#[derive(Default)]
struct Totals {
    done: u64,
    failed: u64,
    cancelled: u64,
    expired: u64,
    cancel_unconfirmed: u64,
    refused_queue_full: u64,
    refused_held: u64,
}

/// What the pool has seen of one kind of work: how many, how they ended, and how long they waited and ran.
#[derive(Default, Serialize)]
struct KindStats {
    submitted: u64,
    refused_queue_full: u64,
    refused_held: u64,
    done: u64,
    failed: u64,
    cancelled: u64,
    expired: u64,
    cancel_unconfirmed: u64,
    wait_ms_total: u64,
    wait_ms_max: u64,
    run_ms_total: u64,
    run_ms_max: u64,
}

/// The state of one pool and every rule about it, with no clock and no tasks: the caller passes the instant (a duration since the pool was made).
pub struct Core {
    limits: Limits,
    next_id: u64,
    queue: VecDeque<Entry>,
    slots: Vec<Slot>,
    records: VecDeque<JobRecord>,
    woken: Vec<usize>,
    totals: Totals,
    kinds: BTreeMap<&'static str, KindStats>,
    /// The wall-clock time the pool's own clock started at (for the times a person reads).
    unix_origin_ms: u64,
    closed: bool,
    /// Unresolved entries from an earlier run that no worker is left to hold (the worker count was reduced): kept, shown, journalled, and each one blocks admission until resolved.
    orphans: Vec<Held>,
    /// Why the recovery state could not be read: nothing is admitted until a person acknowledges it.
    unrecovered: Option<String>,
    /// The journal could not be written, so what is held is not on file: while anything is held, nothing is admitted.
    undurable: bool,
}

fn ms(d: Duration) -> u64 {
    d.as_millis() as u64
}

impl Core {
    pub fn new(limits: Limits) -> Core {
        let workers = limits.workers.max(1);
        Core {
            limits: Limits { workers, ..limits },
            next_id: 1,
            queue: VecDeque::new(),
            slots: (0..workers).map(|_| Slot::Idle).collect(),
            records: VecDeque::new(),
            woken: Vec::new(),
            totals: Totals::default(),
            kinds: BTreeMap::new(),
            unix_origin_ms: 0,
            closed: false,
            orphans: Vec::new(),
            unrecovered: None,
            undurable: false,
        }
    }

    fn record_mut(&mut self, id: u64) -> Option<&mut JobRecord> {
        self.records.iter_mut().rev().find(|r| r.id == id)
    }

    #[cfg(test)]
    pub fn record(&self, id: u64) -> Option<&JobRecord> {
        self.records.iter().rev().find(|r| r.id == id)
    }

    fn stat(&mut self, kind: &'static str) -> &mut KindStats {
        self.kinds.entry(kind).or_default()
    }

    fn count(&mut self, kind: &'static str, state: JobState) {
        let (total, kind_total): (&mut u64, fn(&mut KindStats) -> &mut u64) = match state {
            JobState::Done => (&mut self.totals.done, |k| &mut k.done),
            JobState::Failed => (&mut self.totals.failed, |k| &mut k.failed),
            JobState::Cancelled => (&mut self.totals.cancelled, |k| &mut k.cancelled),
            JobState::Expired => (&mut self.totals.expired, |k| &mut k.expired),
            JobState::CancelUnconfirmed => (&mut self.totals.cancel_unconfirmed, |k| &mut k.cancel_unconfirmed),
            JobState::Queued | JobState::Running => return,
        };
        *total += 1;
        *kind_total(self.kinds.entry(kind).or_default()) += 1;
    }

    fn note_wait(&mut self, kind: &'static str, wait_ms: u64) {
        let k = self.stat(kind);
        k.wait_ms_total += wait_ms;
        k.wait_ms_max = k.wait_ms_max.max(wait_ms);
    }

    fn note_run(&mut self, kind: &'static str, run_ms: u64) {
        let k = self.stat(kind);
        k.run_ms_total += run_ms;
        k.run_ms_max = k.run_ms_max.max(run_ms);
    }

    fn trim(&mut self) {
        while self.records.len() > self.limits.history {
            match self.records.iter().position(|r| r.state.terminal()) {
                Some(i) => {
                    self.records.remove(i);
                }
                None => break,
            }
        }
    }

    pub fn set_unix_origin(&mut self, unix_ms: u64) {
        self.unix_origin_ms = unix_ms;
    }

    /// Workers held by an earlier run of the terminal (read from the journal): each starts held, with no handle on the process, in its own worker when that is free and otherwise in the first free one. An entry
    /// with no worker left to hold it (the worker count was reduced) is **kept apart** and still blocks admission: unresolved records do not depend on how many workers are configured now. Returns how many entries there were.
    pub fn restore(&mut self, held: &[HeldEntry]) -> usize {
        for h in held {
            self.next_id = self.next_id.max(h.job + 1);
            self.records.push_back(JobRecord {
                id: h.job,
                kind: "previous_run",
                state: JobState::CancelUnconfirmed,
                reason: Some("previous_run_unresolved".into()),
                resolution: None,
                deadline_ms: 0,
                submitted_ms: 0,
                queue_wait_ms: None,
                run_ms: None,
                pid: h.pid,
                child: h.child,
                overrides: Vec::new(),
                abandoned: false,
            });
            let entry = Held {
                job: h.job,
                kind: "previous_run",
                since: Duration::ZERO,
                since_unix_ms: h.since_unix_ms,
                attempts: 0,
                intended: JobState::Failed,
                reason: "previous_run_unresolved",
                requested: None,
                child: Some(ChildInfo { seq: h.child.unwrap_or(0), pid: h.pid }),
                origin: Origin::PreviousRun,
            };
            let own = h.slot.filter(|s| matches!(self.slots.get(*s), Some(Slot::Idle)));
            match own.or_else(|| self.slots.iter().position(|s| matches!(s, Slot::Idle))) {
                Some(i) => self.slots[i] = Slot::Retained(entry),
                None => self.orphans.push(entry),
            }
            self.totals.cancel_unconfirmed += 1;
            self.stat("previous_run").cancel_unconfirmed += 1;
        }
        held.len()
    }

    /// The held workers, and the held entries that have no worker, as they go in the journal.
    pub fn held_entries(&self) -> Vec<HeldEntry> {
        let entry = |slot: Option<usize>, h: &Held| HeldEntry { slot, job: h.job, kind: h.kind.to_string(), pid: h.child.and_then(|c| c.pid), child: h.child.map(|c| c.seq), since_unix_ms: h.since_unix_ms };
        let mut out: Vec<HeldEntry> = self
            .slots
            .iter()
            .enumerate()
            .filter_map(|(i, s)| match s {
                Slot::Retained(h) => Some(entry(Some(i), h)),
                _ => None,
            })
            .collect();
        out.extend(self.orphans.iter().map(|h| entry(None, h)));
        out
    }

    /// The recovery state could not be read (the reason), or is acknowledged (None).
    pub fn set_unrecovered(&mut self, why: Option<String>) {
        self.unrecovered = why;
    }

    /// Whether the journal's last write failed (set by the pool before each submission). With a child held, that means a restart would not know about it.
    pub fn set_undurable(&mut self, undurable: bool) {
        self.undurable = undurable;
    }

    fn has_held(&self) -> bool {
        !self.orphans.is_empty() || self.slots.iter().any(|s| matches!(s, Slot::Retained(_)))
    }

    /// An idle child (its worker is stopping) could not be confirmed gone: it is held under an entry of its own, so that it is journalled and quarantines a restart like any other unresolved child.
    pub fn retain_idle(&mut self, now: Duration, slot: usize, info: ChildInfo) {
        let id = self.next_id;
        self.next_id += 1;
        self.records.push_back(JobRecord {
            id,
            kind: "idle_child",
            state: JobState::CancelUnconfirmed,
            reason: Some("idle_child_unconfirmed_at_shutdown".into()),
            resolution: None,
            deadline_ms: 0,
            submitted_ms: ms(now),
            queue_wait_ms: None,
            run_ms: None,
            pid: info.pid,
            child: Some(info.seq),
            overrides: Vec::new(),
            abandoned: false,
        });
        self.slots[slot] = Slot::Retained(Held {
            job: id,
            kind: "idle_child",
            since: now,
            since_unix_ms: self.unix_origin_ms + ms(now),
            attempts: 2,
            intended: JobState::Cancelled,
            reason: "idle_child_unconfirmed_at_shutdown",
            requested: None,
            child: Some(info),
            origin: Origin::NoHandle,
        });
        self.totals.cancel_unconfirmed += 1;
        self.stat("idle_child").cancel_unconfirmed += 1;
    }

    /// A caller went away. A job that is waiting is cancelled (nothing else moves); one a worker was given but has not started is stopped before it starts; one that is running is let finish and its result
    /// is discarded (it is read-only work, and killing it would cost the worker's warm child).
    pub fn abandon(&mut self, now: Duration, id: u64) {
        if self.queue.iter().any(|e| e.id == id) {
            self.cancel(now, id);
            return;
        }
        for s in &self.slots {
            let stop = match s {
                Slot::Assigned(a) if a.id == id => &a.stop,
                Slot::Busy { id: b, stop, .. } if *b == id => stop,
                _ => continue,
            };
            stop.abandon();
            if let Some(r) = self.record_mut(id) {
                r.abandoned = true;
            }
            return;
        }
    }
    pub fn is_closed(&self) -> bool {
        self.closed
    }

    /// Submit a job. Refused at once when the pool is closed, when every worker is held, or when no worker is free and every place is taken; otherwise it has an ID and either a worker or a place.
    pub fn enqueue(&mut self, now: Duration, kind: &'static str, payload: Value, deadline: Duration, tx: Option<oneshot::Sender<Outcome>>) -> Result<u64, Refusal> {
        if self.closed {
            return Err(Refusal::Closed);
        }
        if self.unrecovered.is_some() {
            self.totals.refused_held += 1;
            self.stat(kind).refused_held += 1;
            return Err(Refusal::Unrecovered);
        }
        if self.undurable && self.has_held() {
            self.totals.refused_held += 1;
            self.stat(kind).refused_held += 1;
            return Err(Refusal::Unrecovered);
        }
        if let Some(job) = self.orphans.first().map(|h| h.job) {
            self.totals.refused_held += 1;
            self.stat(kind).refused_held += 1;
            return Err(Refusal::Held { job });
        }
        self.sweep(now); // a place held by a job that has already expired is free
        if let Some(job) = self.slots.iter().find_map(|s| if let Slot::Retained(h) = s { Some(h.job) } else { None }) {
            if self.slots.iter().all(|s| matches!(s, Slot::Retained(_))) {
                self.totals.refused_held += 1;
                self.stat(kind).refused_held += 1;
                return Err(Refusal::Held { job });
            }
        }
        let idle = self.slots.iter().any(|s| matches!(s, Slot::Idle));
        if !idle && self.queue.len() >= self.limits.capacity {
            self.totals.refused_queue_full += 1;
            self.stat(kind).refused_queue_full += 1;
            return Err(Refusal::QueueFull { capacity: self.limits.capacity });
        }
        let id = self.next_id;
        self.next_id += 1;
        self.stat(kind).submitted += 1;
        self.records.push_back(JobRecord {
            id,
            kind,
            state: JobState::Queued,
            reason: None,
            resolution: None,
            deadline_ms: ms(deadline),
            submitted_ms: ms(now),
            queue_wait_ms: None,
            run_ms: None,
            pid: None,
            child: None,
            overrides: Vec::new(),
            abandoned: false,
        });
        self.queue.push_back(Entry { id, kind, payload, submitted: now, deadline, tx });
        self.trim();
        self.dispatch(now);
        Ok(id)
    }

    /// Give waiting jobs to free workers, in the order they came. A job whose deadline has passed is expired first and never starts.
    fn dispatch(&mut self, now: Duration) {
        self.sweep(now);
        while !self.queue.is_empty() {
            let Some(i) = self.slots.iter().position(|s| matches!(s, Slot::Idle)) else { break };
            let e = self.queue.pop_front().expect("not empty");
            let wait = ms(now.saturating_sub(e.submitted));
            if let Some(r) = self.record_mut(e.id) {
                r.state = JobState::Running;
                r.queue_wait_ms = Some(wait);
            }
            self.note_wait(e.kind, wait);
            self.slots[i] = Slot::Assigned(Assigned { id: e.id, kind: e.kind, payload: e.payload, submitted: e.submitted, deadline: e.deadline, tx: e.tx, stop: Stop::new() });
            self.woken.push(i);
        }
    }

    /// Expire every waiting job whose deadline has passed (`now >= submitted + deadline`); returns their IDs.
    pub fn sweep(&mut self, now: Duration) -> Vec<u64> {
        let mut gone = Vec::new();
        let mut kept = VecDeque::new();
        for mut e in std::mem::take(&mut self.queue) {
            if now >= e.submitted + e.deadline {
                if let Some(tx) = e.tx.take() {
                    let _ = tx.send(Outcome::Expired("deadline_before_start"));
                }
                let wait = ms(now.saturating_sub(e.submitted));
                if let Some(r) = self.record_mut(e.id) {
                    r.state = JobState::Expired;
                    r.reason = Some("deadline_before_start".into());
                    r.queue_wait_ms = Some(wait);
                }
                self.note_wait(e.kind, wait);
                self.count(e.kind, JobState::Expired);
                gone.push(e.id);
            } else {
                kept.push_back(e);
            }
        }
        self.queue = kept;
        gone
    }

    /// Cancel a job: a waiting one is removed and its caller told (nothing else moves); a running one (or one a worker has been given) has its stop signal set and stays as it is until its child is gone.
    pub fn cancel(&mut self, now: Duration, id: u64) -> Cancelled {
        if let Some(pos) = self.queue.iter().position(|e| e.id == id) {
            let mut e = self.queue.remove(pos).expect("found");
            if let Some(tx) = e.tx.take() {
                let _ = tx.send(Outcome::Cancelled);
            }
            let wait = ms(now.saturating_sub(e.submitted));
            if let Some(r) = self.record_mut(id) {
                r.state = JobState::Cancelled;
                r.reason = Some("cancelled_while_queued".into());
                r.queue_wait_ms = Some(wait);
            }
            self.note_wait(e.kind, wait);
            self.count(e.kind, JobState::Cancelled);
            return Cancelled::Queued;
        }
        for s in &self.slots {
            let stop = match s {
                Slot::Assigned(a) if a.id == id => &a.stop,
                Slot::Busy { id: b, stop, .. } if *b == id => stop,
                _ => continue,
            };
            stop.trip();
            return Cancelled::Running;
        }
        Cancelled::Unknown
    }

    /// Close the pool: nothing new is accepted, what waits is cancelled, what a worker has been given but not picked up is cancelled, and what runs has its stop signal set.
    pub fn close(&mut self, now: Duration) {
        if self.closed {
            return;
        }
        self.closed = true;
        for mut e in std::mem::take(&mut self.queue) {
            if let Some(tx) = e.tx.take() {
                let _ = tx.send(Outcome::Cancelled);
            }
            let wait = ms(now.saturating_sub(e.submitted));
            if let Some(r) = self.record_mut(e.id) {
                r.state = JobState::Cancelled;
                r.reason = Some("pool_closed".into());
                r.queue_wait_ms = Some(wait);
            }
            self.note_wait(e.kind, wait);
            self.count(e.kind, JobState::Cancelled);
        }
        for i in 0..self.slots.len() {
            match std::mem::replace(&mut self.slots[i], Slot::Idle) {
                Slot::Assigned(mut a) => {
                    if let Some(tx) = a.tx.take() {
                        let _ = tx.send(Outcome::Cancelled);
                    }
                    if let Some(r) = self.record_mut(a.id) {
                        r.state = JobState::Cancelled;
                        r.reason = Some("cancelled_before_start".into());
                        r.run_ms = Some(0);
                    }
                    self.count(a.kind, JobState::Cancelled);
                }
                busy @ Slot::Busy { .. } => {
                    if let Slot::Busy { stop, .. } = &busy {
                        stop.trip();
                    }
                    self.slots[i] = busy;
                }
                other => self.slots[i] = other,
            }
        }
    }

    /// The job given to this worker, if it has not picked it up yet.
    pub fn take_assigned(&mut self, slot: usize) -> Option<Assigned> {
        match std::mem::replace(self.slots.get_mut(slot)?, Slot::Idle) {
            Slot::Assigned(a) => {
                self.slots[slot] = Slot::Busy { id: a.id, kind: a.kind, child: None, stop: a.stop.clone() };
                Some(a)
            }
            other => {
                self.slots[slot] = other;
                None
            }
        }
    }

    /// The child a worker is running a job on (its launch number and process ID): shown in the status and kept with the job's record.
    pub fn set_child(&mut self, slot: usize, id: u64, info: ChildInfo) {
        if let Some(Slot::Busy { id: b, child, .. }) = self.slots.get_mut(slot) {
            if *b == id {
                *child = Some(info);
            }
        }
        if let Some(r) = self.record_mut(id) {
            r.pid = info.pid;
            r.child = Some(info.seq);
        }
    }

    /// The workers that were given a job since the last call (they are to be woken).
    pub fn take_woken(&mut self) -> Vec<usize> {
        std::mem::take(&mut self.woken)
    }

    /// A job has ended on `slot`. With `how.retain` the child could not be confirmed gone: the job is `cancel_unconfirmed` and the worker is held; otherwise the worker is free and the next job takes it.
    pub fn finish(&mut self, now: Duration, slot: usize, id: u64, how: Finish) -> Vec<usize> {
        let child = match &self.slots[slot] {
            Slot::Busy { child, .. } => *child,
            _ => None,
        };
        let kind = self.record_mut(id).map(|r| r.kind).unwrap_or("unknown");
        let mut run_ms = 0;
        if let Some(r) = self.record_mut(id) {
            let started = r.submitted_ms + r.queue_wait_ms.unwrap_or(0);
            run_ms = ms(now).saturating_sub(started);
            r.run_ms = Some(run_ms);
            r.reason = Some(how.reason.into());
            r.state = if how.retain { JobState::CancelUnconfirmed } else { how.state };
        }
        self.note_run(kind, run_ms);
        if how.retain {
            self.count(kind, JobState::CancelUnconfirmed);
            self.slots[slot] = Slot::Retained(Held {
                job: id,
                kind,
                since: now,
                since_unix_ms: self.unix_origin_ms + ms(now),
                attempts: 1,
                intended: how.state,
                reason: how.reason,
                requested: None,
                child,
                origin: Origin::ThisRun,
            });
        } else {
            self.count(kind, how.state);
            self.slots[slot] = Slot::Idle;
        }
        self.dispatch(now);
        self.take_woken()
    }

    /// A held worker is free: its child was confirmed gone, or a person said so. The job becomes what it would have been, with the way it was resolved on record.
    pub fn release(&mut self, now: Duration, slot: usize, resolution: &'static str) -> Vec<usize> {
        if let Slot::Retained(h) = std::mem::replace(&mut self.slots[slot], Slot::Idle) {
            if let Some(r) = self.record_mut(h.job) {
                r.state = h.intended;
                r.resolution = Some(resolution.into());
            }
            self.count(h.kind, h.intended);
        }
        self.dispatch(now);
        self.take_woken()
    }

    /// A person's request about a held entry, with who they are and why. A worker that has a hold task to ask is woken (`Resolved::Wake`); one with no process handle (held from an earlier run, or an idle child whose
    /// handle is gone) and an entry with no worker are freed here and now when a person says the process is gone (`Resolved::Done`), and cannot be asked to try again.
    pub fn request_resolution(&mut self, now: Duration, job: u64, how: Resolution, note: OverrideNote) -> Result<Resolved, ResolveError> {
        if matches!(how, Resolution::AcknowledgeRecovery | Resolution::RetryJournal) {
            return Err(ResolveError::NotBlocked);
        }
        let record = |core: &mut Core, note: OverrideNote| {
            let (at_ms, at_unix_ms) = (ms(now), core.unix_origin_ms + ms(now));
            if let Some(r) = core.record_mut(job) {
                r.overrides.push(OverrideRecord { action: how.name(), operator: note.operator, reason: note.reason, at_ms, at_unix_ms });
            }
        };
        let slot = self.slots.iter().position(|s| matches!(s, Slot::Retained(h) if h.job == job));
        if let Some(i) = slot {
            let Slot::Retained(h) = &mut self.slots[i] else { unreachable!("matched above") };
            if h.origin.handleless() {
                if how == Resolution::Retry {
                    return Err(ResolveError::NoHandle);
                }
                record(self, note);
                return Ok(Resolved::Done(self.release(now, i, "resolved_by_person")));
            }
            h.requested = Some(how);
            record(self, note);
            return Ok(Resolved::Wake(i));
        }
        if let Some(i) = self.orphans.iter().position(|h| h.job == job) {
            if how == Resolution::Retry {
                return Err(ResolveError::NoHandle);
            }
            record(self, note);
            let h = self.orphans.remove(i);
            if let Some(r) = self.record_mut(job) {
                r.state = h.intended;
                r.resolution = Some("resolved_by_person".into());
            }
            self.count(h.kind, h.intended);
            return Ok(Resolved::Done(Vec::new()));
        }
        Err(ResolveError::NotRetained)
    }
    pub fn take_resolution(&mut self, slot: usize) -> Option<Resolution> {
        match self.slots.get_mut(slot)? {
            Slot::Retained(h) => h.requested.take(),
            _ => None,
        }
    }

    pub fn note_attempt(&mut self, slot: usize) {
        if let Some(Slot::Retained(h)) = self.slots.get_mut(slot) {
            h.attempts += 1;
        }
    }

    #[cfg(test)]
    pub fn queued_ids(&self) -> Vec<u64> {
        self.queue.iter().map(|e| e.id).collect()
    }

    #[cfg(test)]
    pub fn slot_view(&self, slot: usize) -> SlotView {
        match &self.slots[slot] {
            Slot::Idle => SlotView::Idle,
            Slot::Assigned(a) => SlotView::Busy(a.id),
            Slot::Busy { id, .. } => SlotView::Busy(*id),
            Slot::Retained(h) => SlotView::Retained(h.job),
        }
    }

    /// Everything the status route shows: capacity, who waits and for how long, who runs and on which child, which workers are held and why, the recent jobs (newest first), the totals and the totals per kind.
    pub fn status(&self, now: Duration) -> Value {
        let running: Vec<u64> = self.slots.iter().filter_map(|s| match s { Slot::Assigned(a) => Some(a.id), Slot::Busy { id, .. } => Some(*id), _ => None }).collect();
        let retained: Vec<Value> = self
            .slots
            .iter()
            .enumerate()
            .filter_map(|(i, s)| match s {
                Slot::Retained(h) => Some(json!({
                    "slot": i, "job": h.job, "kind": h.kind, "since_ms": ms(now.saturating_sub(h.since)), "since_unix_ms": h.since_unix_ms, "attempts": h.attempts,
                    "intended": h.intended, "reason": h.reason, "pid": h.child.and_then(|c| c.pid), "child": h.child.map(|c| c.seq), "origin": h.origin,
                })),
                _ => None,
            })
            .collect();
        let retained: Vec<Value> = retained
            .into_iter()
            .chain(self.orphans.iter().map(|h| {
                json!({
                    "slot": Value::Null, "job": h.job, "kind": h.kind, "since_ms": ms(now.saturating_sub(h.since)), "since_unix_ms": h.since_unix_ms, "attempts": h.attempts,
                    "intended": h.intended, "reason": h.reason, "pid": h.child.and_then(|c| c.pid), "child": h.child.map(|c| c.seq), "origin": h.origin,
                })
            }))
            .collect();
        let workers_detail: Vec<Value> = self
            .slots
            .iter()
            .enumerate()
            .map(|(i, s)| match s {
                Slot::Idle => json!({"slot": i, "state": "idle"}),
                Slot::Assigned(a) => json!({"slot": i, "state": "assigned", "job": a.id, "kind": a.kind}),
                Slot::Busy { id, kind, child, .. } => json!({"slot": i, "state": "running", "job": id, "kind": kind, "pid": child.and_then(|c| c.pid), "child": child.map(|c| c.seq)}),
                Slot::Retained(h) => json!({"slot": i, "state": "held", "job": h.job, "kind": h.kind, "pid": h.child.and_then(|c| c.pid), "child": h.child.map(|c| c.seq), "origin": h.origin}),
            })
            .collect();
        let queued: Vec<Value> = self.queue.iter().map(|e| json!({"id": e.id, "kind": e.kind, "waiting_ms": ms(now.saturating_sub(e.submitted)), "remaining_ms": ms((e.submitted + e.deadline).saturating_sub(now))})).collect();
        let t = &self.totals;
        json!({
            "workers": self.slots.len(),
            "capacity": self.limits.capacity,
            "closed": self.closed,
            "queued": queued,
            "running": running,
            "workers_detail": workers_detail,
            "retained": retained,
            "recent": self.records.iter().rev().collect::<Vec<_>>(),
            "totals": {"done": t.done, "failed": t.failed, "cancelled": t.cancelled, "expired": t.expired, "cancel_unconfirmed": t.cancel_unconfirmed, "refused_queue_full": t.refused_queue_full, "refused_held": t.refused_held},
            "kinds": self.kinds,
        })
    }
}

// ======================================================================= the journal

/// The held children, on file: so that a restart knows about a process the terminal could not confirm gone. Nothing of what was asked goes in it.
struct Journal {
    path: PathBuf,
    /// Test seams: a hook that runs after the snapshot is taken and before the file is written, and a count of writes to fail.
    #[cfg(test)]
    before_write: Mutex<Option<Arc<dyn Fn() + Send + Sync>>>,
    #[cfg(test)]
    fail_writes: AtomicUsize,
}

impl Journal {
    fn new(path: PathBuf) -> Journal {
        Journal {
            path,
            #[cfg(test)]
            before_write: Mutex::new(None),
            #[cfg(test)]
            fail_writes: AtomicUsize::new(0),
        }
    }

    /// The name a write goes to before it is moved into place: its own, so two writes (of this pool or of another process) never share a file.
    fn temp_path(&self) -> PathBuf {
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        self.path.with_extension(format!("{}-{}.tmp", std::process::id(), SEQUENCE.fetch_add(1, Ordering::SeqCst)))
    }

    /// Keep an unreadable file beside the new one, under a name that says when.
    fn put_aside(&self, at_unix_ms: u64) -> std::io::Result<()> {
        if !self.path.exists() {
            return Ok(());
        }
        std::fs::rename(&self.path, self.path.with_extension(format!("unreadable-{at_unix_ms}")))
    }

    fn load(&self) -> Result<Vec<HeldEntry>, String> {
        let text = match std::fs::read_to_string(&self.path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(e) => return Err(format!("could not read the journal: {}", e.kind())),
        };
        let v: Value = serde_json::from_str(&text).map_err(|_| "the journal is not valid JSON".to_string())?;
        if v["version"] != 1 {
            return Err("the journal has a version this terminal does not know".into());
        }
        serde_json::from_value(v["held"].clone()).map_err(|_| "the journal's held list is unreadable".to_string())
    }

    /// Written whole and moved into place, so a reader never sees half of it.
    fn store(&self, pool: &str, held: &[HeldEntry]) -> Result<(), String> {
        #[cfg(test)]
        {
            let hook = self.before_write.lock().expect("hook").clone();
            if let Some(h) = hook {
                h();
            }
            if self.fail_writes.load(Ordering::SeqCst) > 0 {
                self.fail_writes.fetch_sub(1, Ordering::SeqCst);
                return Err("injected write failure".into());
            }
        }
        let body = json!({"version": 1, "pool": pool, "held": held}).to_string();
        if let Some(dir) = self.path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("could not make the journal's folder: {}", e.kind()))?;
        }
        let tmp = self.temp_path();
        let moved = std::fs::write(&tmp, body).map_err(|e| format!("could not write the journal: {}", e.kind())).and_then(|_| std::fs::rename(&tmp, &self.path).map_err(|e| format!("could not move the journal into place: {}", e.kind())));
        if moved.is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
        moved
    }
}

/// The journal and everything about publishing it, for a pool or an admission. Publishing is one transaction: the snapshot of what is held and the write of it happen under one gate, so a later state is never
/// overwritten by an earlier one; a write that fails is reported and the next one carries the whole current state; recovery state that cannot be read blocks admission until a person acknowledges it.
pub(crate) struct Recovery {
    journal: Option<Journal>,
    /// Taken around snapshot and write together. (Lock order: this first, then whatever the snapshot locks; nothing that holds those locks asks for this one.)
    gate: Mutex<()>,
    error: Mutex<Option<String>>,
    /// Why the file could not be read; set when it is loaded, cleared by an acknowledgement.
    unreadable: Mutex<Option<String>>,
    acks: Mutex<Vec<OverrideRecord>>,
}

impl Recovery {
    pub(crate) fn new(path: Option<PathBuf>) -> Recovery {
        Recovery { journal: path.map(Journal::new), gate: Mutex::new(()), error: Mutex::new(None), unreadable: Mutex::new(None), acks: Mutex::new(Vec::new()) }
    }
    pub(crate) fn enabled(&self) -> bool {
        self.journal.is_some()
    }
    /// What was held when the last run ended. A file that cannot be read is **not** read as nothing held: it blocks (see `blocked`).
    pub(crate) fn load(&self) -> Vec<HeldEntry> {
        match self.journal.as_ref().map(Journal::load) {
            Some(Ok(held)) => held,
            Some(Err(why)) => {
                *self.unreadable.lock().expect("journal state") = Some(why);
                Vec::new()
            }
            None => Vec::new(),
        }
    }
    pub(crate) fn blocked(&self) -> Option<String> {
        self.unreadable.lock().expect("journal state").clone()
    }
    /// Snapshot and write, as one transaction.
    pub(crate) fn persist(&self, pool: &str, snapshot: impl FnOnce() -> Vec<HeldEntry>) {
        let Some(journal) = &self.journal else { return };
        let _transaction = self.gate.lock().expect("journal gate");
        let held = snapshot();
        *self.error.lock().expect("journal state") = journal.store(pool, &held).err();
    }
    pub(crate) fn error(&self) -> Option<String> {
        self.error.lock().expect("journal state").clone()
    }
    /// A person accepts that the file could not be read: it is put aside (kept, not deleted) and recorded. Nothing changes if it cannot be put aside.
    pub(crate) fn acknowledge(&self, note: OverrideNote, at_ms: u64, at_unix_ms: u64) -> Result<(), ResolveError> {
        let _transaction = self.gate.lock().expect("journal gate");
        let mut unreadable = self.unreadable.lock().expect("journal state");
        if unreadable.is_none() {
            return Err(ResolveError::NotBlocked);
        }
        if let Some(journal) = &self.journal {
            journal.put_aside(at_unix_ms).map_err(|_| ResolveError::JournalError)?;
        }
        *unreadable = None;
        self.acks.lock().expect("journal state").push(OverrideRecord { action: Resolution::AcknowledgeRecovery.name(), operator: note.operator, reason: note.reason, at_ms, at_unix_ms });
        Ok(())
    }
    /// A person asks for the journal to be written again (it could not be, and something is held). The same transaction as any write; nothing is admitted by it. Refused when no write has failed.
    pub(crate) fn retry(&self, pool: &str, snapshot: impl FnOnce() -> Vec<HeldEntry>, note: OverrideNote, at_ms: u64, at_unix_ms: u64) -> Result<(), ResolveError> {
        let Some(journal) = &self.journal else { return Err(ResolveError::NotBlocked) };
        let _transaction = self.gate.lock().expect("journal gate");
        if self.error.lock().expect("journal state").is_none() {
            return Err(ResolveError::NotBlocked);
        }
        let held = snapshot();
        let written = journal.store(pool, &held);
        let failed = written.is_err();
        *self.error.lock().expect("journal state") = written.err();
        if failed {
            return Err(ResolveError::JournalError);
        }
        self.acks.lock().expect("journal state").push(OverrideRecord { action: Resolution::RetryJournal.name(), operator: note.operator, reason: note.reason, at_ms, at_unix_ms });
        Ok(())
    }
    pub(crate) fn status(&self) -> Value {
        json!({"blocked": self.blocked(), "acknowledgements": *self.acks.lock().expect("journal state")})
    }
}
fn unix_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

// ======================================================================= children

/// One child process (or a fake one in a test).
pub trait Live: Send + 'static {
    /// True when it said it was ready within `limit`.
    fn ready(&mut self, limit: Duration) -> impl Future<Output = bool> + Send;
    /// Send one request and wait for its answer: None when the child closed its output or could not be written to. It has no time limit of its own: the caller's deadline drops this future.
    fn request(&mut self, payload: &Value) -> impl Future<Output = Option<Value>> + Send;
    /// Ask it to end (politely).
    fn terminate(&mut self);
    /// True when it has exited, waiting up to `within`.
    fn wait_exit(&mut self, within: Duration) -> impl Future<Output = bool> + Send;
    /// End it by force.
    fn kill(&mut self);
    /// The operating system's number for the process, when it has one (a person needs it to check that the process is really gone).
    fn pid(&self) -> Option<u32> {
        None
    }
}

pub trait Launcher: Send + Sync + 'static {
    type Live: Live;
    /// Start a child (it is not ready yet), or None when it cannot be started.
    fn spawn(&self) -> Option<Self::Live>;
}

/// Terminate, wait, kill, wait. True only when the exit was confirmed.
async fn end<P: Live>(child: &mut P, grace: Duration) -> bool {
    child.terminate();
    if child.wait_exit(grace).await {
        return true;
    }
    child.kill();
    child.wait_exit(grace).await
}

/// The project's Python workers: a child speaking JSON lines (`{"ready": true}` once, then one reply per request, matched by `id`).
pub struct ProcessLauncher {
    command: Vec<String>,
    stderr: fn() -> Stdio,
}

impl ProcessLauncher {
    pub fn new(command: Vec<String>, stderr: fn() -> Stdio) -> Self {
        ProcessLauncher { command, stderr }
    }
}

pub struct ProcessLive {
    child: Child,
    /// Closing it asks a worker that reads lines to end.
    stdin: Option<ChildStdin>,
    out: Lines<BufReader<ChildStdout>>,
    next_id: u64,
}

impl Launcher for ProcessLauncher {
    type Live = ProcessLive;
    fn spawn(&self) -> Option<ProcessLive> {
        let mut cmd = Command::new(self.command.first()?);
        // `kill_on_drop` ends a child whose handle this process drops while it is alive. It is not a guarantee when this process itself dies (a crash or a kill leaves its children running): the journal
        // and the explicit resolution are the answer to that, not a cleanup.
        cmd.args(&self.command[1..]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr((self.stderr)()).kill_on_drop(true);
        let mut child = cmd.spawn().ok()?;
        let stdin = child.stdin.take()?;
        let out = BufReader::new(child.stdout.take()?).lines();
        Some(ProcessLive { child, stdin: Some(stdin), out, next_id: 1 })
    }
}

impl Live for ProcessLive {
    async fn ready(&mut self, limit: Duration) -> bool {
        tokio::time::timeout(limit, async {
            while let Ok(Some(line)) = self.out.next_line().await {
                if serde_json::from_str::<Value>(&line).ok().is_some_and(|v| v["ready"] == true) {
                    return true;
                }
            }
            false
        })
        .await
        .unwrap_or(false)
    }

    async fn request(&mut self, payload: &Value) -> Option<Value> {
        let id = self.next_id;
        self.next_id += 1;
        let mut request = payload.clone();
        request["id"] = json!(id);
        let line = request.to_string() + "\n";
        let stdin = self.stdin.as_mut()?;
        stdin.write_all(line.as_bytes()).await.ok()?;
        stdin.flush().await.ok()?;
        while let Ok(Some(line)) = self.out.next_line().await {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                if v["id"] == id {
                    return Some(v);
                }
            }
        }
        None
    }

    fn terminate(&mut self) {
        self.stdin = None;
    }

    async fn wait_exit(&mut self, within: Duration) -> bool {
        matches!(tokio::time::timeout(within, self.child.wait()).await, Ok(Ok(_)))
    }

    fn kill(&mut self) {
        let _ = self.child.start_kill();
    }

    fn pid(&self) -> Option<u32> {
        self.child.id()
    }
}

// ======================================================================= the pool

struct Inner<L: Launcher> {
    name: String,
    launcher: L,
    limits: Limits,
    core: Arc<Mutex<Core>>,
    wakes: Vec<Arc<Notify>>,
    origin: Instant,
    started: AtomicBool,
    /// How many children have been launched (the next one's launch number is this plus one).
    launched: AtomicU64,
    /// Workers that have stopped, and the children they ended or could not end while the pool was being shut down.
    /// How many workers have stopped, as a value every shutdown caller can wait on (a notification would wake only one of them).
    exited: tokio::sync::watch::Sender<usize>,
    ended_at_shutdown: AtomicUsize,
    unconfirmed_at_shutdown: AtomicUsize,
    recovery: Arc<Recovery>,
    restored: usize,
}

impl<L: Launcher> Drop for Inner<L> {
    fn drop(&mut self) {
        // the last holder (a pool, a ticket or a worker) is gone: every parked worker is told, so none is left waiting on a pool nobody can reach
        for n in &self.wakes {
            n.notify_one();
        }
    }
}

impl<L: Launcher> Inner<L> {
    fn now(&self) -> Duration {
        self.origin.elapsed()
    }
    fn wake(&self, slots: Vec<usize>) {
        for i in slots {
            self.wakes[i].notify_one();
        }
    }
    fn closed(&self) -> bool {
        self.core.lock().expect("pool state").is_closed()
    }
    fn remains(&self) -> Remains {
        Remains { name: self.name.clone(), core: self.core.clone(), recovery: self.recovery.clone(), origin: self.origin }
    }
    fn persist(&self) {
        self.recovery.persist(&self.name, || self.core.lock().expect("pool state").held_entries());
    }
    fn finish_job(&self, slot: usize, id: u64, how: Finish) {
        let woken = self.core.lock().expect("pool state").finish(self.now(), slot, id, how);
        self.wake(woken);
        if how.retain {
            self.persist();
        }
    }
    fn release(&self, slot: usize, resolution: &'static str) {
        let woken = self.core.lock().expect("pool state").release(self.now(), slot, resolution);
        self.wake(woken);
        self.persist();
    }
    fn abandon(&self, id: u64) {
        self.core.lock().expect("pool state").abandon(self.now(), id);
    }
    fn cancel(&self, id: u64) -> bool {
        let found = self.core.lock().expect("pool state").cancel(self.now(), id);
        !matches!(found, Cancelled::Unknown)
    }
    /// What became of a child that was being ended: counted for the shutdown report when the pool is closing.
    fn note_end(&self, confirmed: bool) {
        if self.closed() {
            let counter = if confirmed { &self.ended_at_shutdown } else { &self.unconfirmed_at_shutdown };
            counter.fetch_add(1, Ordering::SeqCst);
        }
    }
    fn worker_stopped(&self) {
        self.exited.send_modify(|n| *n += 1);
    }
}

/// How a job's time is given: a duration from now, or the instant it is up.
enum Deadline {
    In(Duration),
    At(Instant),
}

/// What a worker keeps of its pool after the pool itself is gone (dropped with nobody having shut it down): enough to put the quarantine of a child it could not end on file. Without it the record of such a
/// child would die with the pool, and a restart would put new work beside it.
#[derive(Clone)]
struct Remains {
    name: String,
    core: Arc<Mutex<Core>>,
    recovery: Arc<Recovery>,
    origin: Instant,
}

impl Remains {
    fn persist(&self) {
        self.recovery.persist(&self.name, || self.core.lock().expect("pool state").held_entries());
    }
    /// An idle child could not be confirmed gone as its worker stopped: it is held and journalled.
    fn retain_idle(&self, slot: usize, info: ChildInfo) {
        self.core.lock().expect("pool state").retain_idle(self.origin.elapsed(), slot, info);
        self.persist();
    }
}

/// A bounded pool of workers behind one queue.
pub struct Pool<L: Launcher> {
    inner: Arc<Inner<L>>,
}

/// A submitted job. Awaiting `result` gives its outcome; dropping the ticket (the caller went away) abandons it: a waiting job is cancelled, one that has not started is stopped before it starts, and one that is
/// running is let finish with its result discarded (explicit cancellation is `Pool::cancel`).
pub struct Ticket<L: Launcher> {
    pub id: u64,
    /// The total time the job was given, from its submission, wait included.
    #[cfg_attr(not(test), allow(dead_code))]
    pub advertised: Duration,
    deadline_at: Instant,
    rx: Option<oneshot::Receiver<Outcome>>,
    inner: Arc<Inner<L>>,
    done: bool,
}

impl<L: Launcher> Ticket<L> {
    pub async fn result(mut self) -> Outcome {
        let mut rx = self.rx.take().expect("awaited once");
        let mut timer = false;
        let out = loop {
            tokio::select! {
                r = &mut rx => break r.unwrap_or(Outcome::Failed("lost")),
                _ = tokio::time::sleep_until(self.deadline_at), if !timer => {
                    // a job still waiting at its deadline expires now, whoever else is or is not looking
                    timer = true;
                    let now = self.inner.now();
                    self.inner.core.lock().expect("pool state").sweep(now);
                }
            }
        };
        self.done = true;
        out
    }
}

impl<L: Launcher> Drop for Ticket<L> {
    fn drop(&mut self) {
        if !self.done {
            self.inner.abandon(self.id);
        }
    }
}

impl<L: Launcher> Pool<L> {
    pub fn new(name: &str, launcher: L, limits: Limits) -> Self {
        Self::build(name, launcher, limits, None)
    }

    /// As `new`, with the held children recorded in a file so a restart knows about them (see the module notes). A worker found held there starts held; a file that cannot be read is reported in the status.
    pub fn with_journal(name: &str, launcher: L, limits: Limits, path: PathBuf) -> Self {
        Self::build(name, launcher, limits, Some(path))
    }

    fn build(name: &str, launcher: L, limits: Limits, journal: Option<PathBuf>) -> Self {
        let workers = limits.workers.max(1);
        let limits = Limits { workers, ..limits };
        let mut core = Core::new(limits);
        core.set_unix_origin(unix_ms());
        let recovery = Recovery::new(journal);
        let restored = core.restore(&recovery.load());
        core.set_unrecovered(recovery.blocked());
        Pool {
            inner: Arc::new(Inner {
                name: name.to_string(),
                launcher,
                limits,
                core: Arc::new(Mutex::new(core)),
                wakes: (0..workers).map(|_| Arc::new(Notify::new())).collect(),
                origin: Instant::now(),
                started: AtomicBool::new(false),
                launched: AtomicU64::new(0),
                exited: tokio::sync::watch::channel(0).0,
                ended_at_shutdown: AtomicUsize::new(0),
                unconfirmed_at_shutdown: AtomicUsize::new(0),
                recovery: Arc::new(recovery),
                restored,
            }),
        }
    }

    fn ensure_workers(&self) {
        if !self.inner.started.swap(true, Ordering::SeqCst) {
            for i in 0..self.inner.wakes.len() {
                tokio::spawn(run_slot(Arc::downgrade(&self.inner), self.inner.remains(), i, self.inner.wakes[i].clone(), self.inner.limits.grace));
            }
        }
    }

    /// Submit a job of `kind` with `deadline` (or the pool's) from now. Refused at once when the queue is full, every worker is held, or the pool is closed. Must be called inside the runtime.
    pub fn enqueue(&self, kind: &'static str, payload: Value, deadline: Option<Duration>) -> Result<Ticket<L>, Refusal> {
        self.submit(kind, payload, Deadline::In(deadline.unwrap_or(self.inner.limits.deadline)))
    }

    fn submit(&self, kind: &'static str, payload: Value, deadline: Deadline) -> Result<Ticket<L>, Refusal> {
        self.ensure_workers();
        let (tx, rx) = oneshot::channel();
        let now = self.inner.now();
        let deadline = match deadline {
            Deadline::In(d) => d,
            Deadline::At(at) => at.saturating_duration_since(self.inner.origin + now),
        };
        let (id, woken) = {
            let mut core = self.inner.core.lock().expect("pool state");
            // admission is refused while a held child is not on file (the journal could not be written): a restart would not know about it
            core.set_undurable(self.inner.recovery.error().is_some());
            let id = core.enqueue(now, kind, payload, deadline, Some(tx))?;
            (id, core.take_woken())
        };
        self.inner.wake(woken);
        Ok(Ticket { id, advertised: deadline, deadline_at: self.inner.origin + now + deadline, rx: Some(rx), inner: self.inner.clone(), done: false })
    }

    /// As `enqueue`, with the job's deadline given as an instant: the **same absolute deadline** the request began with, not a duration worked out earlier. An instant already past gives a job that expires before any
    /// child is started for it.
    pub fn enqueue_until(&self, kind: &'static str, payload: Value, at: Instant) -> Result<Ticket<L>, Refusal> {
        self.submit(kind, payload, Deadline::At(at))
    }

    /// Cancel a job by its ID (true when it was waiting or running). The callers' way is to drop the ticket; this is the same by number.
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn cancel(&self, id: u64) -> bool {
        self.inner.cancel(id)
    }

    pub fn status(&self) -> Value {
        let mut v = self.inner.core.lock().expect("pool state").status(self.inner.now());
        v["pool"] = json!(self.inner.name);
        let error = self.inner.recovery.error();
        let held = v["retained"].as_array().is_some_and(|r| !r.is_empty());
        v["journal"] = json!({"enabled": self.inner.recovery.enabled(), "error": error, "restored": self.inner.restored, "blocking": error.is_some() && held});
        v["recovery"] = self.inner.recovery.status();
        v
    }

    /// Free a held worker (or a held entry): a person says its process is gone, or asks for another attempt to end it; or accepts that the recovery state could not be read. Who asked, and why, is recorded.
    pub fn resolve(&self, job: u64, how: Resolution, note: OverrideNote) -> Result<(), ResolveError> {
        if how == Resolution::AcknowledgeRecovery {
            let now = self.inner.now();
            let unix = self.inner.core.lock().expect("pool state").unix_origin_ms + ms(now);
            self.inner.recovery.acknowledge(note, ms(now), unix)?;
            self.inner.core.lock().expect("pool state").set_unrecovered(None);
            self.inner.persist(); // the fresh file starts from what is held now
            return Ok(());
        }
        if how == Resolution::RetryJournal {
            let now = self.inner.now();
            let unix = self.inner.core.lock().expect("pool state").unix_origin_ms + ms(now);
            return self.inner.recovery.retry(&self.inner.name, || self.inner.core.lock().expect("pool state").held_entries(), note, ms(now), unix);
        }
        self.ensure_workers(); // a worker that was asked to try again has a hold task only once the workers are started
        let resolved = self.inner.core.lock().expect("pool state").request_resolution(self.inner.now(), job, how, note)?;
        match resolved {
            Resolved::Wake(slot) => self.inner.wakes[slot].notify_one(),
            Resolved::Done(woken) => {
                self.inner.wake(woken);
                self.inner.persist();
            }
        }
        Ok(())
    }
    /// End the pool: refuse new jobs, cancel the waiting and the running ones, end every child (terminate, wait, kill, wait) and stop the workers. Returns when they have stopped, with what could not be confirmed gone.
    pub async fn shutdown(&self) -> ShutdownReport {
        let inner = &self.inner;
        inner.core.lock().expect("pool state").close(inner.now());
        for n in &inner.wakes {
            n.notify_one();
        }
        if inner.started.load(Ordering::SeqCst) {
            let mut stopped = inner.exited.subscribe();
            let _ = stopped.wait_for(|n| *n >= inner.wakes.len()).await; // the value is read when waiting begins, so a worker that stopped earlier is seen, and every caller sees it
        }
        ShutdownReport { ended: inner.ended_at_shutdown.load(Ordering::SeqCst), unconfirmed: inner.unconfirmed_at_shutdown.load(Ordering::SeqCst) }
    }

    /// Test seam: make the next `n` journal writes fail.
    #[cfg(test)]
    pub fn fail_next_journal_writes(&self, n: usize) {
        if let Some(j) = &self.inner.recovery.journal {
            j.fail_writes.store(n, Ordering::SeqCst);
        }
    }

    /// Start every worker's child now through the queue (so a request does not wait for the start); returns when they are ready or have failed.
    pub async fn warm(&self) {
        let tickets: Vec<_> = (0..self.inner.limits.workers).filter_map(|_| self.enqueue("warm", Value::Null, Some(self.inner.limits.start_limit)).ok()).collect();
        for t in tickets {
            let _ = t.result().await;
        }
    }
}

/// A child a worker holds, with who it is.
struct Running<C> {
    child: C,
    info: ChildInfo,
}

enum Step {
    Reply(Value),
    NotReady,
    NoAnswer,
    Cancelled,
    Deadline,
}

enum Next {
    Job(Assigned),
    Idle,
    Stop,
}

/// One worker: waits for a job, runs it on its child (started when needed, kept while it is healthy), and holds on to a child that could not be confirmed gone. It stops when the pool is shut down or when no
/// pool, ticket or worker is left to reach it, and ends the child it still holds then.
async fn run_slot<L: Launcher>(weak: Weak<Inner<L>>, remains: Remains, slot: usize, wake: Arc<Notify>, grace: Duration) {
    let mut live: Option<Running<L::Live>> = None;
    loop {
        let Some(inner) = weak.upgrade() else { break };
        let next = {
            let mut core = inner.core.lock().expect("pool state");
            if core.is_closed() {
                Next::Stop
            } else if let Some(job) = core.take_assigned(slot) {
                Next::Job(job)
            } else {
                Next::Idle
            }
        };
        match next {
            Next::Job(job) => live = run_job(&inner, slot, job, live.take(), &wake).await,
            Next::Stop => break,
            Next::Idle => {
                drop(inner);
                wake.notified().await;
            }
        }
    }
    if let Some(mut running) = live.take() {
        let confirmed = end(&mut running.child, grace).await;
        if let Some(inner) = weak.upgrade() {
            inner.note_end(confirmed);
        }
        if !confirmed {
            // held and journalled through what this worker kept of its pool (`remains`), so that it survives the pool being dropped; and before the worker is counted as stopped, so a shutdown report never outruns the file
            remains.retain_idle(slot, running.info);
        }
    }
    if let Some(inner) = weak.upgrade() {
        inner.worker_stopped();
    }
}

async fn run_job<L: Launcher>(inner: &Arc<Inner<L>>, slot: usize, mut job: Assigned, mut live: Option<Running<L::Live>>, wake: &Arc<Notify>) -> Option<Running<L::Live>> {
    let limits = inner.limits;
    let deadline_at = inner.origin + job.submitted + job.deadline;
    let warm = job.payload.is_null();
    let mut tx = job.tx.take();
    let tell = |tx: &mut Option<oneshot::Sender<Outcome>>, outcome: Outcome| {
        if let Some(t) = tx.take() {
            let _ = t.send(outcome);
        }
    };
    // a stop condition is looked at before anything is started or sent: a cancelled job, or one whose deadline passed while its worker was being woken, never reaches a child
    let before = if job.stop.is_set() || job.stop.is_abandoned() {
        Some((Outcome::Cancelled, JobState::Cancelled, "cancelled_before_start"))
    } else if inner.now() >= job.submitted + job.deadline {
        Some((Outcome::Expired("deadline_before_start"), JobState::Expired, "deadline_before_start"))
    } else {
        None
    };
    if let Some((outcome, state, reason)) = before {
        tell(&mut tx, outcome);
        inner.finish_job(slot, job.id, Finish { state, reason, retain: false });
        return live; // a warm child that was not used is kept
    }
    let fresh = live.is_none();
    if fresh {
        match inner.launcher.spawn() {
            Some(child) => {
                let info = ChildInfo { seq: inner.launched.fetch_add(1, Ordering::SeqCst) + 1, pid: child.pid() };
                live = Some(Running { child, info });
            }
            None => {
                tell(&mut tx, Outcome::Failed("start_failed"));
                inner.finish_job(slot, job.id, Finish { state: JobState::Failed, reason: "start_failed", retain: false });
                return None;
            }
        }
    }
    let info = live.as_ref().expect("a child").info;
    inner.core.lock().expect("pool state").set_child(slot, job.id, info);
    // (the child's start has its own limit; the job's deadline bounds it as well, as a branch of the select below, so a child that ignores its limit cannot outlast the deadline)
    let start_budget = limits.start_limit;
    let ready = AtomicBool::new(!fresh);
    let running = live.as_mut().expect("a child");
    let stop = job.stop.clone();
    let step = {
        let ready = &ready;
        let payload = &job.payload;
        let (stop_seen, until) = (stop.clone(), job.submitted + job.deadline);
        let work = async move {
            if fresh {
                if !running.child.ready(start_budget).await {
                    return Step::NotReady;
                }
                ready.store(true, Ordering::SeqCst);
            }
            if warm {
                return Step::Reply(Value::Null);
            }
            // the stop conditions again, before the request is sent: a child that became ready at the very moment of a cancellation or a deadline is not given the work
            if stop_seen.is_set() {
                return Step::Cancelled;
            }
            if inner.now() >= until {
                return Step::Deadline;
            }
            match running.child.request(payload).await {
                Some(v) => Step::Reply(v),
                None => Step::NoAnswer,
            }
        };
        // stop conditions first: against a child that is ready at the same instant they win
        tokio::select! {
            biased;
            _ = stop.tripped() => Step::Cancelled,
            _ = tokio::time::sleep_until(deadline_at) => Step::Deadline,
            s = work => s,
        }
    };
    let deadline_passed = inner.now() >= job.submitted + job.deadline;
    let during_start = fresh && !ready.load(Ordering::SeqCst);
    let mut running = live.take().expect("a child");
    let (outcome, state, reason) = match step {
        Step::Reply(v) => {
            let ok = warm || v["ok"] == true;
            tell(&mut tx, Outcome::Reply(v));
            inner.finish_job(slot, job.id, Finish { state: if ok { JobState::Done } else { JobState::Failed }, reason: if ok { "ok" } else { "worker_error" }, retain: false });
            return Some(running); // a healthy child is kept for the next job
        }
        Step::NoAnswer => {
            // the request was sent: the caller is told only once the child is known to be gone (or not to be), so nothing it does next runs beside work that may still be going
            let confirmed = end(&mut running.child, limits.grace).await;
            inner.note_end(confirmed);
            if confirmed {
                tell(&mut tx, Outcome::Failed("no_answer"));
                inner.finish_job(slot, job.id, Finish { state: JobState::Failed, reason: "no_answer", retain: false });
                return None;
            }
            tell(&mut tx, Outcome::Unconfirmed("no_answer"));
            inner.finish_job(slot, job.id, Finish { state: JobState::Failed, reason: "no_answer", retain: true });
            hold(inner, slot, running.child, wake).await;
            return None;
        }
        Step::NotReady if deadline_passed => (Outcome::Expired("deadline_during_start"), JobState::Expired, "deadline_during_start"),
        Step::NotReady => (Outcome::Failed("start_failed"), JobState::Failed, "start_failed"),
        Step::Cancelled => (Outcome::Cancelled, JobState::Cancelled, "cancelled_while_running"),
        Step::Deadline if during_start => (Outcome::Expired("deadline_during_start"), JobState::Expired, "deadline_during_start"),
        Step::Deadline => (Outcome::Expired("deadline_exceeded"), JobState::Expired, "deadline_exceeded"),
    };
    tell(&mut tx, outcome); // the caller is told now; the child is dealt with next
    let confirmed = end(&mut running.child, limits.grace).await;
    inner.note_end(confirmed);
    if confirmed {
        inner.finish_job(slot, job.id, Finish { state, reason, retain: false });
        return None;
    }
    inner.finish_job(slot, job.id, Finish { state, reason, retain: true });
    hold(inner, slot, running.child, wake).await;
    None
}

/// A child that could not be confirmed gone keeps its worker. Every `recheck` (or when a person asks) the termination is tried again; the worker is freed when the exit is confirmed or a person says the process is
/// gone. When the pool is shut down it is tried once more, and a child still not confirmed stays held (and in the journal).
async fn hold<L: Launcher>(inner: &Arc<Inner<L>>, slot: usize, mut child: L::Live, wake: &Arc<Notify>) {
    let limits = inner.limits;
    loop {
        let woken = tokio::time::timeout(limits.recheck, wake.notified()).await.is_ok();
        if inner.closed() {
            let confirmed = end(&mut child, limits.grace).await;
            inner.note_end(confirmed);
            if confirmed {
                inner.release(slot, "confirmed_at_shutdown");
            }
            return;
        }
        if !woken && inner.recovery.error().is_some() {
            inner.persist(); // what is held could not be written: it is tried again at every recheck, with nothing admitted by it
        }
        let resolution = inner.core.lock().expect("pool state").take_resolution(slot);
        if resolution == Some(Resolution::ConfirmedGone) {
            drop(child); // a person has checked: the pool stops waiting for a confirmation it will not get
            inner.release(slot, "resolved_by_person");
            return;
        }
        if woken && resolution.is_none() {
            continue;
        }
        inner.core.lock().expect("pool state").note_attempt(slot);
        if end(&mut child, limits.grace).await {
            inner.release(slot, if resolution == Some(Resolution::Retry) { "confirmed_after_retry" } else { "confirmed_exit_later" });
            return;
        }
    }
}

// ======================================================================= what the routes see

/// What the status and resolve routes see of a pool (or of an admission).
pub trait PoolView: Send + Sync {
    fn name(&self) -> String;
    fn status(&self) -> Value;
    fn resolve(&self, job: u64, how: Resolution, note: OverrideNote) -> Result<(), ResolveError>;
    /// End what it runs (a pool does; an admission has nothing to end).
    fn shutdown(&self) -> Pin<Box<dyn Future<Output = ShutdownReport> + Send + '_>> {
        Box::pin(async { ShutdownReport::default() })
    }
}

impl<L: Launcher> PoolView for Pool<L> {
    fn name(&self) -> String {
        self.inner.name.clone()
    }
    fn status(&self) -> Value {
        Pool::status(self)
    }
    fn resolve(&self, job: u64, how: Resolution, note: OverrideNote) -> Result<(), ResolveError> {
        Pool::resolve(self, job, how, note)
    }
    fn shutdown(&self) -> Pin<Box<dyn Future<Output = ShutdownReport> + Send + '_>> {
        Box::pin(Pool::shutdown(self))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RegistryError {
    NoPool,
    NotRetained,
    NoHandle,
    NotBlocked,
    JournalError,
}

/// Every pool (and admission) of the terminal, by name.
#[derive(Default)]
pub struct WorkRegistry {
    pools: Mutex<Vec<Arc<dyn PoolView>>>,
}

impl WorkRegistry {
    pub fn new() -> WorkRegistry {
        WorkRegistry::default()
    }
    pub fn register(&self, pool: Arc<dyn PoolView>) {
        self.pools.lock().expect("registry").push(pool);
    }
    pub fn status(&self) -> Value {
        let pools: Vec<Value> = self.pools.lock().expect("registry").iter().map(|p| p.status()).collect();
        json!({"pools": pools})
    }
    pub fn resolve(&self, pool: &str, job: u64, how: Resolution, note: OverrideNote) -> Result<(), RegistryError> {
        let found = self.pools.lock().expect("registry").iter().find(|p| p.name() == pool).cloned().ok_or(RegistryError::NoPool)?;
        found.resolve(job, how, note).map_err(|e| match e {
            ResolveError::NotRetained => RegistryError::NotRetained,
            ResolveError::NoHandle => RegistryError::NoHandle,
            ResolveError::NotBlocked => RegistryError::NotBlocked,
            ResolveError::JournalError => RegistryError::JournalError,
        })
    }
    /// Shut every pool down, one after the other, and add up what they report.
    pub async fn shutdown_all(&self) -> ShutdownReport {
        let pools: Vec<Arc<dyn PoolView>> = self.pools.lock().expect("registry").clone();
        let mut total = ShutdownReport::default();
        for p in pools {
            let r = p.shutdown().await;
            total.ended += r.ended;
            total.unconfirmed += r.unconfirmed;
        }
        total
    }
}

// ======================================================================= one deadline and bounded admission for every path

/// One deadline for a whole request: every wait and every attempt (admission, a helper, the command run directly) draws on what is left of it.
#[derive(Clone, Copy, Debug)]
pub struct Budget {
    at: Instant,
}

impl Budget {
    pub fn new(total: Duration) -> Budget {
        Budget { at: Instant::now() + total }
    }
    pub fn remaining(&self) -> Duration {
        self.at.saturating_duration_since(Instant::now())
    }
    pub fn expired(&self) -> bool {
        self.remaining().is_zero()
    }
    /// The instant the request's time is up: handed down as it is, never as a duration worked out earlier, so every layer cuts at the same moment.
    pub fn deadline(&self) -> Instant {
        self.at
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AdmitError {
    /// Every place is taken and every waiting place too: refused at once.
    Full { capacity: usize },
    /// The request's deadline passed while it waited.
    Expired,
    /// Every place is held by a child that could not be confirmed gone (or the recovery state is unreadable or cannot be written).
    Held,
    /// The admission has been shut down: nothing new is accepted.
    Closed,
}

#[derive(Default, Serialize)]
struct AdmissionKind {
    admitted: u64,
    refused_full: u64,
    refused_held: u64,
    expired: u64,
    /// The caller went away while it waited.
    cancelled: u64,
    wait_ms_total: u64,
    wait_ms_max: u64,
    run_ms_total: u64,
    run_ms_max: u64,
}

/// A direct command that could not be confirmed gone, and whose place is therefore still its own.
struct DirectHeld {
    id: u64,
    kind: String,
    pid: Option<u32>,
    since: Duration,
    since_unix_ms: u64,
    attempts: u32,
    requested: Option<Resolution>,
    origin: Origin,
    /// A place was withheld for it at start-up (held by an earlier run), and is given back when a person resolves it.
    forgotten_permit: bool,
    overrides: Vec<OverrideRecord>,
    wake: Arc<Notify>,
}

#[derive(Serialize)]
struct DirectResolved {
    id: u64,
    kind: String,
    pid: Option<u32>,
    resolution: &'static str,
    overrides: Vec<OverrideRecord>,
}

struct AdmissionState {
    waiting: usize,
    kinds: BTreeMap<&'static str, AdmissionKind>,
    held: Vec<DirectHeld>,
    resolved: VecDeque<DirectResolved>,
    next_id: u64,
    /// Shutdown has begun: nothing new is admitted and no command is started.
    closed: bool,
    /// Direct owners still running (a child on its way to being ended, or held and being retried).
    owners: usize,
}

/// How long a direct command is given, after it was asked to end and again after it was killed, to be confirmed gone; and how often a held one is tried again.
const DIRECT_GRACE: Duration = Duration::from_secs(5);
const DIRECT_RECHECK: Duration = Duration::from_secs(15);

struct AdmShared {
    name: String,
    running: usize,
    waiting_cap: usize,
    permits: Arc<Semaphore>,
    state: Mutex<AdmissionState>,
    recovery: Recovery,
    origin: Instant,
    unix_origin_ms: u64,
    /// Set when shutdown begins; every owner watches it.
    closing: tokio::sync::watch::Sender<bool>,
    /// The number of owners, as a value every shutdown caller can wait on (kept in step with `AdmissionState::owners`, under its lock).
    owners_seen: tokio::sync::watch::Sender<usize>,
    ended_at_shutdown: AtomicUsize,
    unconfirmed_at_shutdown: AtomicUsize,
    /// Test seam: awaited by `run_command` after it has decided it may launch and before it starts the process, so a test can hold a launch at exactly that point.
    #[cfg(test)]
    launch_gap: Mutex<Option<LaunchGap>>,
}

#[cfg(test)]
type LaunchGap = Arc<dyn Fn() -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;

/// One direct owner, counted from the moment a command is handed over until its task is gone, so shutdown knows what to wait for.
struct OwnerGuard {
    shared: Arc<AdmShared>,
}

impl Drop for OwnerGuard {
    fn drop(&mut self) {
        let mut st = self.shared.state.lock().expect("admission state");
        st.owners = st.owners.saturating_sub(1);
        self.shared.owners_seen.send_replace(st.owners);
    }
}

/// Resolves when shutdown has begun (at once if it already has).
async fn wait_closed(rx: &mut tokio::sync::watch::Receiver<bool>) {
    let _ = rx.wait_for(|closed| *closed).await;
}

impl AdmShared {
    fn closed(&self) -> bool {
        self.state.lock().expect("admission state").closed
    }
    /// An owner is counted from here until its guard is dropped. Counted even after shutdown began (a child already in hand is still ended by its own owner).
    fn owner(self: &Arc<Self>) -> OwnerGuard {
        let mut st = self.state.lock().expect("admission state");
        st.owners += 1;
        self.owners_seen.send_replace(st.owners);
        OwnerGuard { shared: self.clone() }
    }
    /// An owner for a launch that has not started its process yet, counted **in the same step** as the check that shutdown has not begun: so a launch is either refused here or is an owner shutdown waits for. There is no
    /// moment in which it has passed the check and is not yet counted.
    fn try_owner(self: &Arc<Self>) -> Option<OwnerGuard> {
        let mut st = self.state.lock().expect("admission state");
        if st.closed {
            return None;
        }
        st.owners += 1;
        self.owners_seen.send_replace(st.owners);
        Some(OwnerGuard { shared: self.clone() })
    }
    /// A child ended while shutdown was under way (the one that started it, a deadline cleanup or a recheck that shutdown overlapped) is counted as ended, once: by the owner, at the step that ends it.
    fn note_ended_if_closing(&self, confirmed: bool) {
        if confirmed && self.closed() {
            self.note_end_at_shutdown(true);
        }
    }
    /// What became of a child ended because the terminal is shutting down.
    fn note_end_at_shutdown(&self, confirmed: bool) {
        let counter = if confirmed { &self.ended_at_shutdown } else { &self.unconfirmed_at_shutdown };
        counter.fetch_add(1, Ordering::SeqCst);
    }
    /// The owner is leaving with the child still not confirmed gone: its entry stays held and journalled, and nothing is left to ask, so only a person can free it.
    fn give_up_handle(&self, id: u64) {
        if let Some(h) = self.state.lock().expect("admission state").held.iter_mut().find(|h| h.id == id) {
            h.origin = Origin::NoHandle;
        }
    }
    /// A journal write that failed is tried again (called on every cycle of a held owner), with nothing admitted by it.
    fn retry_persist_if_failed(&self) {
        if self.recovery.error().is_some() {
            self.persist();
        }
    }
    fn held_entries(&self) -> Vec<HeldEntry> {
        let st = self.state.lock().expect("admission state");
        st.held.iter().map(|h| HeldEntry { slot: None, job: h.id, kind: h.kind.clone(), pid: h.pid, child: None, since_unix_ms: h.since_unix_ms }).collect()
    }
    fn persist(&self) {
        self.recovery.persist(&self.name, || self.held_entries());
    }
    fn hold_direct(&self, kind: &'static str, pid: Option<u32>) -> (u64, Arc<Notify>) {
        let wake = Arc::new(Notify::new());
        let id = {
            let mut st = self.state.lock().expect("admission state");
            let id = st.next_id;
            st.next_id += 1;
            let since = self.origin.elapsed();
            st.held.push(DirectHeld { id, kind: kind.to_string(), pid, since, since_unix_ms: self.unix_origin_ms + ms(since), attempts: 2, requested: None, origin: Origin::ThisRun, forgotten_permit: false, overrides: Vec::new(), wake: wake.clone() });
            id
        };
        self.persist();
        (id, wake)
    }
    fn take_requested(&self, id: u64) -> Option<Resolution> {
        self.state.lock().expect("admission state").held.iter_mut().find(|h| h.id == id).and_then(|h| h.requested.take())
    }
    fn note_attempt(&self, id: u64) {
        if let Some(h) = self.state.lock().expect("admission state").held.iter_mut().find(|h| h.id == id) {
            h.attempts += 1;
        }
    }
    /// The held command is gone (confirmed, or a person said so): it leaves the held list and the journal, and its resolution is kept.
    fn free_held(&self, id: u64, resolution: &'static str) {
        {
            let mut st = self.state.lock().expect("admission state");
            if let Some(i) = st.held.iter().position(|h| h.id == id) {
                let h = st.held.remove(i);
                st.resolved.push_back(DirectResolved { id: h.id, kind: h.kind, pid: h.pid, resolution, overrides: h.overrides });
                while st.resolved.len() > 20 {
                    st.resolved.pop_front();
                }
            }
        }
        self.persist();
    }
}

/// A bounded number of requests running, a bounded number waiting, and the wait counted against the request's deadline. Telemetry is per kind of work, as for a pool. A request that runs a command directly
/// hands the command to an owner of its own (`run_direct`): the place is the owner's until the command's exit is **confirmed** (or a person says it is gone), whatever becomes of the request.
pub struct Admission {
    shared: Arc<AdmShared>,
}

/// A place taken in an `Admission`; the time it is held is recorded when it is dropped.
pub struct Permit {
    _permit: tokio::sync::OwnedSemaphorePermit,
    shared: Arc<AdmShared>,
    kind: &'static str,
    since: Instant,
}

impl Drop for Permit {
    fn drop(&mut self) {
        let held = ms(self.since.elapsed());
        let mut st = self.shared.state.lock().expect("admission state");
        let k = st.kinds.entry(self.kind).or_default();
        k.run_ms_total += held;
        k.run_ms_max = k.run_ms_max.max(held);
    }
}

/// A waiting place, given back whenever the wait ends: finished, expired, or **dropped** (the caller went away), so cancelled waiters never use up the places.
struct WaitGuard {
    shared: Arc<AdmShared>,
    kind: &'static str,
    completed: bool,
}

impl Drop for WaitGuard {
    fn drop(&mut self) {
        let mut st = self.shared.state.lock().expect("admission state");
        st.waiting = st.waiting.saturating_sub(1);
        if !self.completed {
            st.kinds.entry(self.kind).or_default().cancelled += 1;
        }
    }
}

impl Admission {
    pub fn new(name: &str, running: usize, waiting: usize) -> Admission {
        Self::build(name, running, waiting, None)
    }

    /// As `new`, with the children held by direct commands recorded in a file, like a pool's (see the module notes): a restart keeps their places held until a person resolves them, and recovery state that
    /// cannot be read blocks admission until a person acknowledges it.
    pub fn with_journal(name: &str, running: usize, waiting: usize, path: PathBuf) -> Admission {
        Self::build(name, running, waiting, Some(path))
    }

    fn build(name: &str, running: usize, waiting: usize, journal: Option<PathBuf>) -> Admission {
        let running = running.max(1);
        let recovery = Recovery::new(journal);
        let permits = Arc::new(Semaphore::new(running));
        let mut state = AdmissionState { waiting: 0, kinds: BTreeMap::new(), held: Vec::new(), resolved: VecDeque::new(), next_id: 1, closed: false, owners: 0 };
        for e in recovery.load() {
            state.next_id = state.next_id.max(e.job + 1);
            let forgotten_permit = permits.clone().try_acquire_owned().map(|p| p.forget()).is_ok();
            state.held.push(DirectHeld { id: e.job, kind: e.kind, pid: e.pid, since: Duration::ZERO, since_unix_ms: e.since_unix_ms, attempts: 0, requested: None, origin: Origin::PreviousRun, forgotten_permit, overrides: Vec::new(), wake: Arc::new(Notify::new()) });
        }
        Admission {
            shared: Arc::new(AdmShared {
                name: name.to_string(),
                running,
                waiting_cap: waiting,
                permits,
                state: Mutex::new(state),
                recovery,
                origin: Instant::now(),
                unix_origin_ms: unix_ms(),
                closing: tokio::sync::watch::channel(false).0,
                owners_seen: tokio::sync::watch::channel(0).0,
                ended_at_shutdown: AtomicUsize::new(0),
                unconfirmed_at_shutdown: AtomicUsize::new(0),
                #[cfg(test)]
                launch_gap: Mutex::new(None),
            }),
        }
    }

    /// Test seam: hold every `run_command` launch at the point between its decision to launch and the start of the process.
    #[cfg(test)]
    pub fn on_launch_gap(&self, f: LaunchGap) {
        *self.shared.launch_gap.lock().expect("hook") = Some(f);
    }

    fn permit(&self, permit: tokio::sync::OwnedSemaphorePermit, kind: &'static str, waited: Duration) -> Permit {
        let mut st = self.shared.state.lock().expect("admission state");
        let k = st.kinds.entry(kind).or_default();
        k.admitted += 1;
        k.wait_ms_total += ms(waited);
        k.wait_ms_max = k.wait_ms_max.max(ms(waited));
        Permit { _permit: permit, shared: self.shared.clone(), kind, since: Instant::now() }
    }

    /// A place, or why not: refused at once when every place is held by a command that could not be confirmed stopped (or the recovery state is unreadable), at once when one is free, refused at once when none
    /// is and no waiting place is either, otherwise after waiting for one for at most what is left of `budget`.
    pub async fn admit(&self, kind: &'static str, budget: &Budget) -> Result<Permit, AdmitError> {
        let sh = &self.shared;
        let started = Instant::now();
        {
            let mut st = sh.state.lock().expect("admission state");
            if st.closed {
                return Err(AdmitError::Closed);
            }
            // blocked: the recovery state is unreadable; or every place is held; or something is held that is not on file (the journal could not be written), so a restart would not know about it
            if sh.recovery.blocked().is_some() || st.held.len() >= sh.running || (!st.held.is_empty() && sh.recovery.error().is_some()) {
                st.kinds.entry(kind).or_default().refused_held += 1;
                return Err(AdmitError::Held);
            }
        }
        if let Ok(p) = sh.permits.clone().try_acquire_owned() {
            return Ok(self.permit(p, kind, Duration::ZERO));
        }
        let mut guard = {
            let mut st = sh.state.lock().expect("admission state");
            if st.waiting >= sh.waiting_cap {
                st.kinds.entry(kind).or_default().refused_full += 1;
                return Err(AdmitError::Full { capacity: sh.waiting_cap });
            }
            st.waiting += 1;
            WaitGuard { shared: sh.clone(), kind, completed: false }
        };
        let got = tokio::time::timeout(budget.remaining(), sh.permits.clone().acquire_owned()).await;
        let waited = started.elapsed();
        guard.completed = true;
        drop(guard);
        match got {
            Ok(Ok(p)) => Ok(self.permit(p, kind, waited)),
            Ok(Err(_)) => Err(AdmitError::Closed), // the places were closed while it waited: the terminal is shutting down
            Err(_) => {
                let mut st = sh.state.lock().expect("admission state");
                let k = st.kinds.entry(kind).or_default();
                k.expired += 1;
                k.wait_ms_total += ms(waited);
                k.wait_ms_max = k.wait_ms_max.max(ms(waited));
                Err(AdmitError::Expired)
            }
        }
    }

    pub fn view(self: &Arc<Self>) -> Arc<dyn PoolView> {
        self.clone()
    }

    /// End the admission: nothing new is admitted or started, every direct child is ended (kill, wait, kill, wait; a held one is tried once more) and either confirmed gone or left held and journalled, and the
    /// callers still waiting for one are told the terminal is shutting down. Returns when every owner is gone, with what could not be confirmed.
    pub async fn shutdown(&self) -> ShutdownReport {
        let sh = &self.shared;
        sh.state.lock().expect("admission state").closed = true;
        sh.permits.close(); // a request waiting for a place is told it is closed
        sh.closing.send_replace(true);
        // the count is a value, read when waiting begins: every caller (not just one) sees it reach zero, and an owner that was already gone is seen
        let mut owners = sh.owners_seen.subscribe();
        let _ = owners.wait_for(|n| *n == 0).await;
        ShutdownReport { ended: sh.ended_at_shutdown.load(Ordering::SeqCst), unconfirmed: sh.unconfirmed_at_shutdown.load(Ordering::SeqCst) }
    }

    /// Test seam: make the next `n` journal writes fail.
    #[cfg(test)]
    pub fn fail_next_journal_writes(&self, n: usize) {
        if let Some(j) = &self.shared.recovery.journal {
            j.fail_writes.store(n, Ordering::SeqCst);
        }
    }

    /// Hand a child to an owner of its own and wait for its result for at most what is left of `budget`. The caller is told at the deadline (`DirectError::Timeout`); the owner then ends the child (kill, wait,
    /// kill, wait) and keeps `permit` until the exit is confirmed. A child that cannot be confirmed gone keeps the place held, listed and journalled, until it is confirmed later or a person resolves it. If the
    /// caller itself goes away the child is let finish (it is read-only work) and the place stays its own until it has.
    pub async fn run_direct<C: DirectChild>(&self, permit: Permit, child: C, kind: &'static str, budget: &Budget) -> Result<DirectOutput, DirectError> {
        let owner = self.shared.owner(); // counted from here, so a shutdown waits for it; one handed over after shutdown began is ended at once by its owner
        self.run_owned(permit, child, kind, budget, owner).await
    }

    async fn run_owned<C: DirectChild>(&self, permit: Permit, child: C, kind: &'static str, budget: &Budget, owner: OwnerGuard) -> Result<DirectOutput, DirectError> {
        let (tx, rx) = oneshot::channel();
        tokio::spawn(direct_owner(self.shared.clone(), permit, child, kind, budget.deadline(), tx, owner));
        rx.await.unwrap_or(Err(DirectError::Timeout))
    }

    /// Start `cmd` as a direct child and run it (see `run_direct`). Not started at all when the terminal is shutting down or the request's time is already up.
    pub async fn run_command(&self, permit: Permit, mut cmd: Command, kind: &'static str, budget: &Budget) -> Result<DirectOutput, DirectError> {
        // counted as an owner in the same step as the check that shutdown has not begun, and kept until the process is handed to its owner task: shutdown cannot return between the check and the start
        let Some(owner) = self.shared.try_owner() else { return Err(DirectError::Shutdown) };
        if budget.expired() {
            self.shared.state.lock().expect("admission state").kinds.entry(kind).or_default().expired += 1;
            return Err(DirectError::Timeout);
        }
        #[cfg(test)]
        {
            let gap = self.shared.launch_gap.lock().expect("hook").clone();
            if let Some(g) = gap {
                g().await;
            }
        }
        // (`kill_on_drop` is only a backstop for a dropped handle while this process is alive; the owner ends the child and confirms it)
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
        let child = cmd.spawn().map_err(|_| DirectError::Spawn)?;
        self.run_owned(permit, ProcessDirect::new(child), kind, budget, owner).await
    }
}

/// What cut a direct child's wait short (or did not).
enum Cut {
    Done(DirectOutput),
    Deadline,
    Shutdown,
}

/// The owner of one direct child: waits for it to finish, for the deadline or for shutdown; ends it if either of those came, and keeps the place until the exit is confirmed. A child that cannot be confirmed gone is
/// held (listed, journalled) and tried again every `DIRECT_RECHECK` until it is confirmed, a person frees it, or the terminal is shut down (one more attempt, then it is left held and on file).
async fn direct_owner<C: DirectChild>(shared: Arc<AdmShared>, permit: Permit, mut child: C, kind: &'static str, deadline_at: Instant, tx: oneshot::Sender<Result<DirectOutput, DirectError>>, _owner: OwnerGuard) {
    let pid = child.pid();
    let mut closing = shared.closing.subscribe();
    let cut = tokio::select! {
        biased;
        _ = wait_closed(&mut closing) => Cut::Shutdown,
        _ = tokio::time::sleep_until(deadline_at) => Cut::Deadline,
        out = child.finished() => Cut::Done(out),
    };
    match cut {
        Cut::Done(out) => {
            let _ = tx.send(Ok(out));
            drop(permit); // it ended by itself: nothing is left running
            return;
        }
        Cut::Shutdown => {
            let _ = tx.send(Err(DirectError::Shutdown));
            let confirmed = end_direct(&mut child).await;
            shared.note_end_at_shutdown(confirmed);
            if !confirmed {
                let (id, _) = shared.hold_direct(kind, pid); // journalled before this owner is gone, so a shutdown never completes ahead of the file
                shared.give_up_handle(id);
            }
            drop(permit);
            return;
        }
        Cut::Deadline => {
            let _ = tx.send(Err(DirectError::Timeout)); // told at the deadline; the cleanup is the owner's
        }
    }
    let confirmed = end_direct(&mut child).await;
    shared.note_ended_if_closing(confirmed);
    if confirmed {
        drop(permit);
        return;
    }
    let (id, wake) = shared.hold_direct(kind, pid);
    loop {
        let (closed, woken) = tokio::select! {
            _ = wait_closed(&mut closing) => (true, false),
            r = tokio::time::timeout(DIRECT_RECHECK, wake.notified()) => (false, r.is_ok()),
        };
        if closed {
            // the terminal is shutting down: one more attempt, and what is still not confirmed stays held and on file
            let confirmed = end_direct(&mut child).await;
            shared.note_end_at_shutdown(confirmed);
            if confirmed {
                shared.free_held(id, "confirmed_at_shutdown");
            } else {
                shared.give_up_handle(id);
            }
            drop(permit);
            return;
        }
        if !woken {
            shared.retry_persist_if_failed(); // at every recheck
        }
        let requested = shared.take_requested(id);
        if requested == Some(Resolution::ConfirmedGone) {
            drop(child); // a person has checked: nobody waits for a confirmation that will not come
            shared.free_held(id, "resolved_by_person");
            drop(permit);
            return;
        }
        if woken && requested.is_none() {
            continue;
        }
        shared.note_attempt(id);
        let confirmed = end_direct(&mut child).await;
        shared.note_ended_if_closing(confirmed);
        if confirmed {
            shared.free_held(id, if requested == Some(Resolution::Retry) { "confirmed_after_retry" } else { "confirmed_exit_later" });
            drop(permit);
            return;
        }
    }
}
/// Kill, wait, kill, wait. True only when the exit was confirmed.
async fn end_direct<C: DirectChild>(child: &mut C) -> bool {
    child.kill();
    if child.wait_exit(DIRECT_GRACE).await {
        return true;
    }
    child.kill();
    child.wait_exit(DIRECT_GRACE).await
}

/// What a direct child printed and whether it succeeded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DirectOutput {
    pub success: bool,
    /// Its exit code, when it ended with one.
    pub code: Option<i32>,
    pub stdout: Vec<u8>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DirectError {
    /// The command could not be started.
    Spawn,
    /// Its deadline passed (the child is ended afterwards, and its place is kept until that is confirmed).
    Timeout,
    /// The terminal is shutting down: the command was not started, or was ended.
    Shutdown,
}

/// A child run directly for one request (not a worker): something that runs to completion, can be killed and can be asked whether it has exited.
pub trait DirectChild: Send + 'static {
    /// Waits for it to end and collects what it printed.
    fn finished(&mut self) -> impl Future<Output = DirectOutput> + Send;
    fn kill(&mut self);
    /// True when it has exited, waiting up to `within`.
    fn wait_exit(&mut self, within: Duration) -> impl Future<Output = bool> + Send;
    fn pid(&self) -> Option<u32>;
}

pub struct ProcessDirect {
    child: Child,
    stdout: Option<ChildStdout>,
}

impl ProcessDirect {
    pub fn new(mut child: Child) -> ProcessDirect {
        let stdout = child.stdout.take();
        ProcessDirect { child, stdout }
    }
}

impl DirectChild for ProcessDirect {
    async fn finished(&mut self) -> DirectOutput {
        use tokio::io::AsyncReadExt;
        let mut out = Vec::new();
        let read = async {
            if let Some(s) = self.stdout.as_mut() {
                let _ = s.read_to_end(&mut out).await;
            }
        };
        let (_, status) = tokio::join!(read, self.child.wait());
        let status = status.ok();
        DirectOutput { success: status.is_some_and(|s| s.success()), code: status.and_then(|s| s.code()), stdout: out }
    }
    fn kill(&mut self) {
        let _ = self.child.start_kill();
    }
    async fn wait_exit(&mut self, within: Duration) -> bool {
        matches!(tokio::time::timeout(within, self.child.wait()).await, Ok(Ok(_)))
    }
    fn pid(&self) -> Option<u32> {
        self.child.id()
    }
}

impl PoolView for Admission {
    fn name(&self) -> String {
        self.shared.name.clone()
    }
    fn shutdown(&self) -> Pin<Box<dyn Future<Output = ShutdownReport> + Send + '_>> {
        Box::pin(Admission::shutdown(self))
    }
    fn status(&self) -> Value {
        let sh = &self.shared;
        let st = sh.state.lock().expect("admission state");
        let now = sh.origin.elapsed();
        let held: Vec<Value> = st
            .held
            .iter()
            .map(|h| json!({"id": h.id, "kind": h.kind, "pid": h.pid, "since_ms": ms(now.saturating_sub(h.since)), "since_unix_ms": h.since_unix_ms, "attempts": h.attempts, "origin": h.origin, "overrides": h.overrides}))
            .collect();
        json!({
            "pool": sh.name, "type": "admission", "workers": sh.running, "capacity": sh.waiting_cap, "waiting": st.waiting,
            "held": held, "resolved": st.resolved, "kinds": st.kinds,
            "recovery": sh.recovery.status(), "journal": {"enabled": sh.recovery.enabled(), "error": sh.recovery.error(), "blocking": sh.recovery.error().is_some() && !st.held.is_empty()},
        })
    }
    /// Free a held direct command, or accept unreadable recovery state. A command held by an earlier run has no handle: only `confirmed_gone` applies.
    fn resolve(&self, job: u64, how: Resolution, note: OverrideNote) -> Result<(), ResolveError> {
        let sh = &self.shared;
        let now = sh.origin.elapsed();
        let at_unix_ms = sh.unix_origin_ms + ms(now);
        if how == Resolution::AcknowledgeRecovery {
            sh.recovery.acknowledge(note, ms(now), at_unix_ms)?;
            sh.persist();
            return Ok(());
        }
        if how == Resolution::RetryJournal {
            return sh.recovery.retry(&sh.name, || sh.held_entries(), note, ms(now), at_unix_ms);
        }
        let record = OverrideRecord { action: how.name(), operator: note.operator, reason: note.reason, at_ms: ms(now), at_unix_ms };
        let mut st = sh.state.lock().expect("admission state");
        let i = st.held.iter().position(|h| h.id == job).ok_or(ResolveError::NotRetained)?;
        if st.held[i].origin.handleless() {
            if how == Resolution::Retry {
                return Err(ResolveError::NoHandle);
            }
            let mut h = st.held.remove(i);
            h.overrides.push(record);
            if h.forgotten_permit {
                // the place withheld for this child follows the children that are still unresolved: it goes to a restored entry that has none (there were more entries than places), and is given back only when
                // every remaining one has its own. Whatever the order they are resolved in, the places left are the places there are minus the children not yet resolved.
                match st.held.iter_mut().find(|o| o.origin.handleless() && !o.forgotten_permit) {
                    Some(other) => other.forgotten_permit = true,
                    None => sh.permits.add_permits(1),
                }
            }
            st.resolved.push_back(DirectResolved { id: h.id, kind: h.kind, pid: h.pid, resolution: "resolved_by_person", overrides: h.overrides });
            drop(st);
            sh.persist();
            return Ok(());
        }
        let h = &mut st.held[i];
        h.requested = Some(how);
        h.overrides.push(record);
        let wake = h.wake.clone();
        drop(st);
        wake.notify_one();
        Ok(())
    }
}
#[cfg(test)]
#[path = "bounded_tests.rs"]
mod tests;
