//! T6 regressions: bounded queued work. The state machine (`Core`) is tested with explicit instants; the pool with fake children and tokio's paused clock, so every deadline below is exact and nothing sleeps.
use super::*;
use std::collections::VecDeque;
use tokio::time::{sleep, Instant};

fn s(n: u64) -> Duration {
    Duration::from_secs(n)
}
fn limits(workers: usize, capacity: usize) -> Limits {
    Limits { workers, capacity, deadline: s(300), start_limit: s(60), grace: s(5), recheck: s(15), history: 50 }
}
const DONE: Finish = Finish { state: JobState::Done, reason: "ok", retain: false };

// ======================================================================= the state machine

fn core(workers: usize, capacity: usize) -> Core {
    Core::new(limits(workers, capacity))
}
fn put(c: &mut Core, now: u64, deadline: u64) -> u64 {
    c.enqueue(s(now), "t", json!({}), s(deadline), None).expect("accepted")
}

#[test]
fn a_submission_past_capacity_is_refused_at_once_and_the_running_and_queued_jobs_are_untouched() {
    let mut c = core(1, 2);
    let (a, b, d) = (put(&mut c, 0, 300), put(&mut c, 0, 300), put(&mut c, 0, 300));
    assert_eq!(c.slot_view(0), SlotView::Busy(a));
    assert_eq!(c.queued_ids(), vec![b, d]);
    assert_eq!(c.enqueue(s(1), "t", json!({}), s(300), None), Err(Refusal::QueueFull { capacity: 2 }));
    assert_eq!(c.slot_view(0), SlotView::Busy(a), "the running job is not disturbed");
    assert_eq!(c.queued_ids(), vec![b, d], "nor are the queued ones, nor their order");
    assert_eq!(c.record(a).unwrap().state, JobState::Running);
    assert_eq!(c.record(b).unwrap().state, JobState::Queued);
    assert_eq!(c.status(s(1))["totals"]["refused_queue_full"], 1);
    assert_eq!(c.record(d + 1), None, "a refused submission leaves no job behind");
}

#[test]
fn capacity_counts_the_jobs_that_wait_not_the_one_that_runs() {
    let mut c = core(1, 0);
    let a = put(&mut c, 0, 300);
    assert_eq!(c.slot_view(0), SlotView::Busy(a), "an idle worker takes the job at once, so a queue of zero still serves one");
    assert_eq!(c.enqueue(s(0), "t", json!({}), s(300), None), Err(Refusal::QueueFull { capacity: 0 }));
    let mut two = core(2, 1);
    let ids: Vec<u64> = (0..3).map(|_| put(&mut two, 0, 300)).collect();
    assert_eq!((two.slot_view(0), two.slot_view(1), two.queued_ids()), (SlotView::Busy(ids[0]), SlotView::Busy(ids[1]), vec![ids[2]]));
    assert!(two.enqueue(s(0), "t", json!({}), s(300), None).is_err());
}

#[test]
fn a_job_whose_deadline_passes_while_queued_expires_never_starts_and_frees_its_place() {
    let mut c = core(1, 1);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 10);
    assert!(c.enqueue(s(1), "t", json!({}), s(300), None).is_err(), "the place is taken");
    assert_eq!(c.sweep(Duration::from_millis(9_999)), Vec::<u64>::new(), "one millisecond before its deadline it is still waiting");
    assert_eq!(c.sweep(s(10)), vec![b], "at its deadline it has expired");
    let r = c.record(b).unwrap();
    assert_eq!((r.state, r.reason.as_deref(), r.queue_wait_ms, r.run_ms), (JobState::Expired, Some("deadline_before_start"), Some(10_000), None));
    assert_eq!(c.slot_view(0), SlotView::Busy(a), "the running job is untouched, and b was never given the slot");
    assert!(c.queued_ids().is_empty());
    let next = c.enqueue(s(11), "t", json!({}), s(300), None).expect("the freed place is usable");
    assert_eq!(c.queued_ids(), vec![next]);
}

#[test]
fn a_queued_job_is_cancelled_and_removed_without_touching_the_running_one_and_the_order_of_the_others_is_kept() {
    let mut c = core(1, 4);
    let ids: Vec<u64> = (0..4).map(|_| put(&mut c, 0, 300)).collect(); // a runs; b, c, d wait
    assert!(matches!(c.cancel(s(5), ids[2]), Cancelled::Queued));
    assert_eq!(c.queued_ids(), vec![ids[1], ids[3]], "c is gone, b and d keep their order");
    let r = c.record(ids[2]).unwrap();
    assert_eq!((r.state, r.reason.as_deref()), (JobState::Cancelled, Some("cancelled_while_queued")));
    assert_eq!(c.slot_view(0), SlotView::Busy(ids[0]));
    assert_eq!(c.record(ids[0]).unwrap().state, JobState::Running, "the running job goes on");
    assert!(matches!(c.cancel(s(5), ids[2]), Cancelled::Unknown), "a second cancel finds nothing");
    c.finish(s(20), 0, ids[0], DONE);
    assert_eq!(c.slot_view(0), SlotView::Busy(ids[1]), "b is next, as before");
    c.finish(s(30), 0, ids[1], DONE);
    assert_eq!(c.slot_view(0), SlotView::Busy(ids[3]), "then d");
}

#[test]
fn cancelling_a_running_job_signals_it_and_changes_nothing_until_its_child_is_gone() {
    let mut c = core(1, 2);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 300);
    assert!(matches!(c.cancel(s(5), a), Cancelled::Running));
    assert_eq!(c.record(a).unwrap().state, JobState::Running);
    assert_eq!(c.slot_view(0), SlotView::Busy(a), "the slot is held until the worker says the child is gone");
    assert_eq!(c.queued_ids(), vec![b]);
    // the same once the worker has picked the job up (the slot is then `Busy`, not merely assigned)
    c.take_assigned(0).expect("picked up");
    assert_eq!(matches!(c.cancel(s(6), a), Cancelled::Running), true, "a job a worker is running is signalled too");
    assert_eq!(c.slot_view(0), SlotView::Busy(a));
}

#[test]
fn a_place_held_by_a_job_that_has_expired_is_free_at_the_next_submission_without_anyone_sweeping() {
    let mut c = core(1, 1);
    let _a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 10); // takes the only place
    assert!(c.enqueue(s(5), "t", json!({}), s(300), None).is_err(), "full while b waits");
    let next = c.enqueue(s(11), "t", json!({}), s(300), None);
    assert_eq!(next.is_ok(), true, "b's deadline passed at 10: its place is free at 11 ({next:?})");
    assert_eq!(c.record(b).unwrap().state, JobState::Expired);
    assert_eq!(c.queued_ids(), vec![next.unwrap()]);
}

#[test]
fn the_second_jobs_deadline_covers_its_wait_so_its_running_time_is_what_remains() {
    let mut c = core(1, 2);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 300);
    c.take_assigned(0).unwrap();
    c.finish(s(200), 0, a, DONE);
    let job = c.take_assigned(0).expect("b has the slot");
    assert_eq!(job.id, b);
    assert_eq!(job.remaining(s(200)), s(100), "300 s advertised, 200 s already spent waiting");
    assert_eq!(job.remaining(s(299)), s(1));
    assert_eq!(job.remaining(s(400)), Duration::ZERO, "never negative");
    let r = c.record(b).unwrap();
    assert_eq!((r.deadline_ms, r.queue_wait_ms), (300_000, Some(200_000)));
}

#[test]
fn queue_wait_and_running_time_are_recorded_per_job() {
    let mut c = core(1, 2);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 3, 300);
    c.finish(s(10), 0, a, DONE);
    c.finish(s(25), 0, b, Finish { state: JobState::Failed, reason: "no_answer", retain: false });
    let (ra, rb) = (c.record(a).unwrap().clone(), c.record(b).unwrap().clone());
    assert_eq!((ra.queue_wait_ms, ra.run_ms, ra.state), (Some(0), Some(10_000), JobState::Done));
    assert_eq!((rb.queue_wait_ms, rb.run_ms, rb.state, rb.reason.as_deref()), (Some(7_000), Some(15_000), JobState::Failed, Some("no_answer")));
    assert_eq!(c.status(s(30))["totals"]["done"], 1);
    assert_eq!(c.status(s(30))["totals"]["failed"], 1);
}

#[test]
fn an_unconfirmed_child_keeps_the_slot_and_no_new_job_takes_it_until_a_person_resolves_it() {
    let mut c = core(1, 2);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 300);
    c.finish(s(30), 0, a, Finish { state: JobState::Expired, reason: "deadline_exceeded", retain: true });
    assert_eq!(c.slot_view(0), SlotView::Retained(a));
    assert_eq!(c.record(a).unwrap().state, JobState::CancelUnconfirmed);
    assert_eq!(c.queued_ids(), vec![b], "b waits: the worker is not free");
    assert_eq!(c.enqueue(s(31), "t", json!({}), s(300), None), Err(Refusal::Held { job: a }), "a worker that cannot be freed takes no new job");
    assert_eq!(c.status(s(31))["totals"]["refused_held"], 1);
    assert_eq!(c.status(s(31))["retained"][0]["job"], a);
    assert_eq!(c.request_resolution(s(31), a, Resolution::ConfirmedGone, note()), Ok(Resolved::Wake(0)));
    assert_eq!(c.take_resolution(0), Some(Resolution::ConfirmedGone));
    c.release(s(40), 0, "resolved_by_person");
    assert_eq!(c.slot_view(0), SlotView::Busy(b), "the slot is free again and b takes it");
    let r = c.record(a).unwrap();
    assert_eq!((r.state, r.reason.as_deref(), r.resolution.as_deref()), (JobState::Expired, Some("deadline_exceeded"), Some("resolved_by_person")));
}

#[test]
fn only_a_retained_job_can_be_resolved_and_other_workers_keep_serving() {
    let mut c = core(2, 1);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 300);
    assert_eq!(c.request_resolution(s(1), a, Resolution::ConfirmedGone, note()), Err(ResolveError::NotRetained), "a running job is not retained");
    assert_eq!(c.request_resolution(s(1), 999, Resolution::Retry, note()), Err(ResolveError::NotRetained));
    c.finish(s(5), 0, a, Finish { state: JobState::Cancelled, reason: "cancelled_while_running", retain: true });
    assert_eq!(c.slot_view(1), SlotView::Busy(b));
    let later = c.enqueue(s(6), "t", json!({}), s(300), None);
    assert!(later.is_ok(), "one worker is held, the other is only busy: the queue still has a place");
    c.finish(s(7), 1, b, DONE);
    assert_eq!(c.slot_view(1), SlotView::Busy(later.unwrap()), "and the free worker serves it");
    assert_eq!(c.slot_view(0), SlotView::Retained(a));
}

// ======================================================================= the pool, with fake children

type Log = Arc<Mutex<Vec<String>>>;

#[derive(Clone, Copy)]
enum Req {
    Answer(Duration),
    Fail(Duration),
    Silent,
    Closes(Duration),
    /// Gives up on the request (no answer) after a while, but the process is not gone: it still has to be ended.
    Drops(Duration),
}
#[derive(Clone, Copy)]
enum Exit {
    Never,
    After(Duration),
}
#[derive(Clone)]
struct Script {
    /// A child whose readiness never comes and which does not honour the limit it is given (only the job's own deadline can end the wait).
    ready_never: bool,
    ready_after: Duration,
    reqs: Vec<Req>,
    on_terminate: Exit,
    on_kill: Exit,
    /// Runs the moment the child reports ready (a test uses it to cancel the job at exactly that point).
    on_ready: Option<Arc<dyn Fn() + Send + Sync>>,
}
impl Script {
    fn new() -> Script {
        Script { ready_never: false, ready_after: Duration::ZERO, reqs: vec![], on_terminate: Exit::After(s(1)), on_kill: Exit::After(Duration::ZERO), on_ready: None }
    }
    fn on_ready(mut self, f: impl Fn() + Send + Sync + 'static) -> Script {
        self.on_ready = Some(Arc::new(f));
        self
    }
    fn ready_after(mut self, d: Duration) -> Script {
        self.ready_after = d;
        self
    }
    fn req(mut self, r: Req) -> Script {
        self.reqs.push(r);
        self
    }
    fn terminate(mut self, e: Exit) -> Script {
        self.on_terminate = e;
        self
    }
    fn kill(mut self, e: Exit) -> Script {
        self.on_kill = e;
        self
    }
    fn never_ready(mut self) -> Script {
        self.ready_never = true;
        self
    }
    fn immortal(self) -> Script {
        self.terminate(Exit::Never).kill(Exit::Never)
    }
}

struct FakeLaunch {
    scripts: Mutex<VecDeque<Option<Script>>>,
    log: Log,
    t0: Instant,
    spawned: Mutex<usize>,
}
struct FakeLive {
    n: usize,
    script: Script,
    reqs: VecDeque<Req>,
    log: Log,
    t0: Instant,
    terminated: Option<Instant>,
    killed: Option<Instant>,
    gone: bool,
}
impl FakeLive {
    fn note(&self, what: &str) {
        self.log.lock().unwrap().push(format!("{what}{}@{}", self.n, self.t0.elapsed().as_secs()));
    }
}
impl Launcher for FakeLaunch {
    type Live = FakeLive;
    fn spawn(&self) -> Option<FakeLive> {
        let script = self.scripts.lock().unwrap().pop_front().unwrap_or(Some(Script::new()))?;
        let mut n = self.spawned.lock().unwrap();
        *n += 1;
        self.log.lock().unwrap().push(format!("spawn{}@{}", *n, self.t0.elapsed().as_secs()));
        Some(FakeLive { n: *n, reqs: script.reqs.iter().copied().collect(), script, log: self.log.clone(), t0: self.t0, terminated: None, killed: None, gone: false })
    }
}
impl Live for FakeLive {
    async fn ready(&mut self, limit: Duration) -> bool {
        if self.script.ready_never {
            return std::future::pending::<bool>().await;
        }
        // (a child with nothing to wait for is ready in the very poll that asks: a stop condition that is already set must beat it, which a timer that needs a second poll would hide)
        let ok = self.script.ready_after.is_zero() || tokio::time::timeout(limit, sleep(self.script.ready_after)).await.is_ok();
        if ok {
            self.note("ready");
            if let Some(f) = &self.script.on_ready {
                f();
            }
        }
        ok
    }
    async fn request(&mut self, payload: &Value) -> Option<Value> {
        self.note("request");
        match self.reqs.pop_front().unwrap_or(Req::Answer(Duration::ZERO)) {
            Req::Answer(d) => {
                if !d.is_zero() {
                    sleep(d).await;
                }
                Some(json!({"ok": true, "result": payload["n"].clone()}))
            }
            Req::Drops(d) => {
                sleep(d).await;
                None
            }
            Req::Fail(d) => {
                sleep(d).await;
                Some(json!({"ok": false, "error": "no such instrument"}))
            }
            Req::Silent => std::future::pending::<Option<Value>>().await,
            Req::Closes(d) => {
                sleep(d).await;
                self.gone = true;
                self.note("exit");
                None
            }
        }
    }
    fn terminate(&mut self) {
        self.note("terminate");
        self.terminated.get_or_insert_with(Instant::now); // the first request counts: asking again does not restart a child's clock
    }
    async fn wait_exit(&mut self, within: Duration) -> bool {
        if self.gone {
            return true;
        }
        let at = [
            self.terminated.and_then(|t| if let Exit::After(d) = self.script.on_terminate { Some(t + d) } else { None }),
            self.killed.and_then(|t| if let Exit::After(d) = self.script.on_kill { Some(t + d) } else { None }),
        ]
        .into_iter()
        .flatten()
        .min();
        match at {
            Some(at) if at <= Instant::now() + within => {
                tokio::time::sleep_until(at).await;
                self.gone = true;
                self.note("exit");
                true
            }
            _ => {
                sleep(within).await;
                false
            }
        }
    }
    fn kill(&mut self) {
        self.note("kill");
        self.killed.get_or_insert_with(Instant::now);
    }
    fn pid(&self) -> Option<u32> {
        Some(1000 + self.n as u32)
    }
}

fn note() -> OverrideNote {
    OverrideNote::new("tester", "checked by hand").expect("a valid note")
}

fn pool_of(l: Limits, scripts: Vec<Option<Script>>) -> (Pool<FakeLaunch>, Log, Instant) {
    let log: Log = Arc::new(Mutex::new(Vec::new()));
    let t0 = Instant::now();
    let launch = FakeLaunch { scripts: Mutex::new(scripts.into()), log: log.clone(), t0, spawned: Mutex::new(0) };
    (Pool::new("test", launch, l), log, t0)
}
fn events(log: &Log) -> Vec<String> {
    log.lock().unwrap().clone()
}
fn n(v: u64) -> Value {
    json!({"n": v})
}
/// Every await in these tests is bounded, so a defect fails the test instead of hanging it.
async fn bounded<F: Future>(f: F) -> F::Output {
    let r = tokio::time::timeout(s(100_000), f).await;
    assert_eq!(r.is_ok(), true, "the test's own bound: the work never finished");
    r.unwrap()
}
fn record(pool: &Pool<FakeLaunch>, id: u64) -> Value {
    pool.status()["recent"].as_array().unwrap().iter().find(|r| r["id"] == id).cloned().unwrap_or(Value::Null)
}

#[tokio::test(start_paused = true)]
async fn the_second_jobs_deadline_includes_its_wait_in_the_queue() {
    let (pool, log, t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Answer(s(200))).req(Req::Answer(s(150))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), None).unwrap();
    assert_eq!((a.advertised, b.advertised), (s(300), s(300)), "the second job is advertised the same total, measured from its submission");
    let (ra, rb) = bounded(async { tokio::join!(a.result(), b.result()) }).await;
    assert_eq!(ra, Outcome::Reply(json!({"ok": true, "result": 1})));
    assert_eq!(rb, Outcome::Expired("deadline_exceeded"), "b waited 200 s, had 100 s, and its answer was due at 350 s");
    assert_eq!(t0.elapsed(), s(300), "the caller is told at the deadline (300 s from submission), not after the child has been dealt with");
    sleep(s(2)).await;
    let ev = events(&log);
    assert_eq!(&ev[ev.len() - 2..], ["terminate1@300", "exit1@301"], "the child was then ended and confirmed gone: {ev:?}");
}

#[tokio::test(start_paused = true)]
async fn a_job_whose_deadline_passes_in_the_queue_expires_before_start_never_reaches_a_child_and_frees_its_place() {
    let (pool, log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Answer(s(200))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), Some(s(50))).unwrap();
    assert!(matches!(pool.enqueue("t", n(3), None), Err(Refusal::QueueFull { capacity: 1 })));
    let hb = tokio::spawn(b.result());
    let rb = bounded(hb).await.unwrap();
    assert_eq!(rb, Outcome::Expired("deadline_before_start"));
    assert_eq!(t0.elapsed(), s(50), "at its own deadline, while the first job still runs");
    let c = pool.enqueue("t", n(4), None).expect("its place is free again");
    let (ra, rc) = bounded(async { tokio::join!(a.result(), c.result()) }).await;
    assert!(matches!(ra, Outcome::Reply(_)) && matches!(rc, Outcome::Reply(_)), "the others are unaffected");
    let requests = events(&log).iter().filter(|e| e.starts_with("request")).count();
    assert_eq!(requests, 2, "b's payload was never sent to a child");
    assert_eq!(record(&pool, 2)["state"], "expired");
}

#[tokio::test(start_paused = true)]
async fn a_submission_past_capacity_is_refused_synchronously_and_the_jobs_already_accepted_finish_normally() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Answer(s(10))).req(Req::Answer(s(10))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), None).unwrap();
    for _ in 0..3 {
        assert!(matches!(pool.enqueue("t", n(9), None), Err(Refusal::QueueFull { capacity: 1 })), "refused without waiting");
    }
    assert_eq!(t0.elapsed(), Duration::ZERO, "no time passed: nothing waited for a place");
    let (ra, rb) = bounded(async { tokio::join!(a.result(), b.result()) }).await;
    assert!(matches!((ra, rb), (Outcome::Reply(_), Outcome::Reply(_))));
    assert_eq!(pool.status()["totals"]["refused_queue_full"], 3);
}

#[tokio::test(start_paused = true)]
async fn dropping_a_queued_ticket_cancels_it_without_touching_the_running_job() {
    let (pool, log, _t0) = pool_of(limits(1, 3), vec![Some(Script::new().req(Req::Answer(s(100))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), None).unwrap();
    let c = pool.enqueue("t", n(3), None).unwrap();
    let (bid, cid) = (b.id, c.id);
    let ha = tokio::spawn(a.result());
    let hc = tokio::spawn(c.result());
    sleep(s(10)).await;
    drop(b);
    assert_eq!(record(&pool, bid)["state"], "cancelled");
    assert_eq!(record(&pool, bid)["reason"], "cancelled_while_queued");
    assert!(!pool.cancel(bid), "it is already gone");
    let (ra, rc) = bounded(async { tokio::join!(ha, hc) }).await;
    assert_eq!(ra.unwrap(), Outcome::Reply(json!({"ok": true, "result": 1})), "the running job finished normally");
    assert_eq!(rc.unwrap(), Outcome::Reply(json!({"ok": true, "result": 3})), "the one behind the cancelled job ran next");
    let ev = events(&log);
    assert!(!ev.iter().any(|e| e.starts_with("terminate") || e.starts_with("kill")), "no child was disturbed: {ev:?}");
    assert_eq!(ev.iter().filter(|e| e.starts_with("spawn")).count(), 1, "one warm child served both");
    let _ = cid;
}

#[tokio::test(start_paused = true)]
async fn cancelling_a_running_job_ends_its_child_and_the_slot_is_released_only_after_the_exit_is_confirmed() {
    let scripts = vec![Some(Script::new().req(Req::Silent).terminate(Exit::After(s(3)))), Some(Script::new())];
    let (pool, log, t0) = pool_of(limits(1, 2), scripts);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), None).unwrap();
    let aid = a.id;
    let hb = tokio::spawn(b.result());
    sleep(s(10)).await;
    assert_eq!(pool.cancel(aid), true); // an explicit cancellation: a caller that merely goes away no longer cancels a running job
    sleep(s(2)).await; // 12: terminate was asked at 10, the child exits at 13
    let ev = events(&log);
    assert_eq!(ev.contains(&"terminate1@10".to_string()), true, "{ev:?}");
    assert_eq!(ev.iter().any(|e| e.starts_with("spawn2")), false, "the next job has not been given the worker yet: {ev:?}");
    assert_eq!(record(&pool, aid)["state"], "running", "not released until the child is gone");
    let rb = bounded(hb).await.unwrap();
    assert!(matches!(rb, Outcome::Reply(_)));
    let ev = events(&log);
    let exit = ev.iter().position(|e| e == "exit1@13").expect("exit confirmed at 13");
    let spawn2 = ev.iter().position(|e| e.starts_with("spawn2")).expect("the next job started");
    assert!(exit < spawn2, "the second child was started only after the first was confirmed gone: {ev:?}");
    assert_eq!(record(&pool, aid)["state"], "cancelled");
    assert!(t0.elapsed() >= s(13));
}

#[tokio::test(start_paused = true)]
async fn a_child_that_ignores_the_polite_request_is_killed_and_the_slot_is_released_after_that_exit() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).terminate(Exit::Never).kill(Exit::After(s(2))))]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let id = a.id;
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_exceeded"));
    sleep(s(20)).await;
    let ev = events(&log);
    assert_eq!(&ev[3..], ["terminate1@30", "kill1@35", "exit1@37"], "terminate, wait, kill, wait: {ev:?}");
    assert_eq!(record(&pool, id)["state"], "expired");
    assert_eq!(pool.status()["retained"].as_array().unwrap().len(), 0, "confirmed: nothing is held");
}

#[tokio::test(start_paused = true)]
async fn an_unconfirmed_child_retains_the_guard_refuses_new_work_and_a_person_can_resolve_it() {
    let (pool, log, t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_exceeded"), "the caller is told at its deadline, not after the child is dealt with");
    assert_eq!(t0.elapsed(), s(30));
    sleep(s(11)).await; // terminate at 30, kill at 35, the second wait ends at 40
    assert_eq!(record(&pool, aid)["state"], "cancel_unconfirmed");
    let st = pool.status();
    assert_eq!(st["retained"][0]["job"], aid, "{st}");
    assert!(matches!(pool.enqueue("t", n(2), None), Err(Refusal::Held { job }) if job == aid), "no new job takes a worker that cannot be freed");
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 1, "no second child was started");
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone, note()), Ok(()));
    sleep(s(1)).await;
    assert_eq!(pool.status()["retained"].as_array().unwrap().len(), 0, "released by the person");
    let r = record(&pool, aid);
    assert_eq!((r["state"].as_str(), r["resolution"].as_str()), (Some("expired"), Some("resolved_by_person")));
    let b = pool.enqueue("t", n(2), None).expect("the worker is free again");
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)));
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 2, "a fresh child, since the old one was given up");
}

#[tokio::test(start_paused = true)]
async fn the_pool_rechecks_a_retained_child_and_releases_the_guard_when_it_is_finally_gone() {
    // ignores terminate, exits 60 s after the kill: not confirmed at 40, confirmed by a later recheck
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).terminate(Exit::Never).kill(Exit::After(s(60))))]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    assert_eq!(record(&pool, aid)["state"], "cancel_unconfirmed");
    assert!(matches!(pool.enqueue("t", n(2), None), Err(Refusal::Held { .. })));
    sleep(s(80)).await;
    assert_eq!(pool.status()["retained"].as_array().unwrap().len(), 0, "released once the exit was confirmed");
    let r = record(&pool, aid);
    assert_eq!((r["state"].as_str(), r["resolution"].as_str()), (Some("expired"), Some("confirmed_exit_later")));
    assert!(pool.enqueue("t", n(2), None).is_ok());
}

#[tokio::test(start_paused = true)]
async fn a_person_can_ask_for_another_attempt_and_only_a_retained_job_can_be_resolved() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    assert_eq!(pool.resolve(aid + 7, Resolution::ConfirmedGone, note()), Err(ResolveError::NotRetained));
    let before = events(&log).iter().filter(|e| e.starts_with("terminate")).count();
    assert_eq!(pool.resolve(aid, Resolution::Retry, note()), Ok(()));
    sleep(s(11)).await;
    let after = events(&log).iter().filter(|e| e.starts_with("terminate")).count();
    assert!(after > before, "another terminate, wait, kill, wait was made on request");
    assert_eq!(record(&pool, aid)["state"], "cancel_unconfirmed", "an immortal child stays unconfirmed");
    assert_eq!(pool.status()["retained"].as_array().unwrap().len(), 1);
}

#[tokio::test(start_paused = true)]
async fn the_deadline_covers_the_childs_startup() {
    // the child needs 100 s to be ready; the job's deadline is 60 s
    let (pool, log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().ready_after(s(100)))]);
    let a = pool.enqueue("t", n(1), Some(s(60))).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_during_start"));
    assert_eq!(t0.elapsed(), s(60));
    sleep(s(5)).await;
    assert!(events(&log).contains(&"terminate1@60".to_string()), "a child that is still starting is ended by the same routine: {:?}", events(&log));
    assert!(events(&log).iter().all(|e| !e.starts_with("request")), "nothing was asked of a child that never became ready");
}

#[tokio::test(start_paused = true)]
async fn a_child_that_ignores_its_start_limit_cannot_outlast_the_jobs_deadline() {
    let (pool, log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().never_ready())]);
    let a = pool.enqueue("t", n(1), Some(s(90))).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_during_start"), "the start limit (60 s) was ignored by the child, so the deadline (90 s) is what ends it, and it is named as such");
    assert_eq!(t0.elapsed(), s(90));
    sleep(s(2)).await;
    assert!(events(&log).contains(&"terminate1@90".to_string()), "{:?}", events(&log));
}

#[tokio::test(start_paused = true)]
async fn startup_has_its_own_limit_inside_the_deadline() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().ready_after(s(100)))]);
    let a = pool.enqueue("t", n(1), Some(s(300))).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("start_failed"));
    assert_eq!(t0.elapsed(), s(60), "the start limit (60 s) applies though the deadline is 300 s");
}

#[tokio::test(start_paused = true)]
async fn a_start_that_cannot_spawn_fails_at_once_and_does_not_hold_the_worker() {
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![None, Some(Script::new())]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("start_failed"));
    let b = pool.enqueue("t", n(2), None).unwrap();
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)), "the next job starts a fresh child");
}

#[tokio::test(start_paused = true)]
async fn a_child_that_dies_mid_job_fails_the_job_and_is_confirmed_gone_before_the_next_starts() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Closes(s(5))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("no_answer"));
    let b = pool.enqueue("t", n(2), None).unwrap();
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)));
    let ev = events(&log);
    assert!(ev.iter().position(|e| e.starts_with("exit1")).unwrap() < ev.iter().position(|e| e.starts_with("spawn2")).unwrap(), "{ev:?}");
}

#[tokio::test(start_paused = true)]
async fn a_healthy_child_is_kept_between_jobs_and_a_failure_reply_does_not_end_it() {
    let (pool, log, _t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Fail(s(1))).req(Req::Answer(s(1))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let aid = a.id;
    let ra = bounded(a.result()).await;
    assert_eq!(ra, Outcome::Reply(json!({"ok": false, "error": "no such instrument"})));
    let b = pool.enqueue("t", n(2), None).unwrap();
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)));
    let ev = events(&log);
    assert_eq!(ev.iter().filter(|e| e.starts_with("spawn")).count(), 1, "{ev:?}");
    assert!(!ev.iter().any(|e| e.starts_with("terminate")), "{ev:?}");
    let r = record(&pool, aid);
    assert_eq!((r["state"].as_str(), r["reason"].as_str()), (Some("failed"), Some("worker_error")));
}

#[tokio::test(start_paused = true)]
async fn per_job_queue_wait_and_running_time_are_in_the_status_for_the_contention_decision() {
    let (pool, _log, _t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Answer(s(40))).req(Req::Answer(s(10))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), None).unwrap();
    let (aid, bid) = (a.id, b.id);
    let _ = bounded(async { tokio::join!(a.result(), b.result()) }).await;
    let (ra, rb) = (record(&pool, aid), record(&pool, bid));
    assert_eq!((ra["queue_wait_ms"].as_u64(), ra["run_ms"].as_u64(), ra["state"].as_str()), (Some(0), Some(40_000), Some("done")));
    assert_eq!((rb["queue_wait_ms"].as_u64(), rb["run_ms"].as_u64(), rb["state"].as_str()), (Some(40_000), Some(10_000), Some("done")));
    assert_eq!(rb["deadline_ms"], 300_000);
    assert_eq!(pool.status()["totals"]["done"], 2);
}

#[tokio::test(start_paused = true)]
async fn warming_starts_the_children_ahead_of_the_first_request_through_the_same_queue() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().ready_after(s(20)))]);
    bounded(pool.warm()).await;
    assert!(events(&log).contains(&"ready1@20".to_string()), "{:?}", events(&log));
    let a = pool.enqueue("t", n(1), None).unwrap();
    assert!(matches!(bounded(a.result()).await, Outcome::Reply(_)));
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 1, "the warm child served the request");
}

// ======================================================================= T6 review: R1 stop conditions and shutdown

fn spawns_and_requests(log: &Log) -> usize {
    events(log).iter().filter(|e| e.starts_with("spawn") || e.starts_with("request")).count()
}

#[tokio::test(start_paused = true)]
async fn a_job_cancelled_before_its_worker_picks_it_up_never_reaches_a_child() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![]);
    let t = pool.enqueue("t", n(1), None).unwrap();
    let id = t.id;
    drop(t); // the worker has been given the job but has not run yet
    sleep(s(1)).await;
    assert_eq!(spawns_and_requests(&log), 0, "no child was started and nothing was sent: {:?}", events(&log));
    let r = record(&pool, id);
    assert_eq!((r["state"].as_str(), r["reason"].as_str()), (Some("cancelled"), Some("cancelled_before_start")), "{r}");
    let b = pool.enqueue("t", n(2), None).unwrap();
    assert_eq!(matches!(bounded(b.result()).await, Outcome::Reply(_)), true, "the worker is not wedged by it");
}

#[tokio::test(start_paused = true)]
async fn a_job_whose_deadline_passed_before_its_worker_picked_it_up_never_reaches_a_child() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![]);
    let t = pool.enqueue("t", n(1), Some(s(5))).unwrap();
    let id = t.id;
    tokio::time::advance(s(5)).await; // the clock moves before the worker has run
    assert_eq!(bounded(t.result()).await, Outcome::Expired("deadline_before_start"));
    sleep(s(1)).await;
    assert_eq!(spawns_and_requests(&log), 0, "{:?}", events(&log));
    let r = record(&pool, id);
    assert_eq!((r["state"].as_str(), r["reason"].as_str()), (Some("expired"), Some("deadline_before_start")), "{r}");
}

#[tokio::test(start_paused = true)]
async fn a_reply_that_arrives_at_the_instant_of_the_deadline_loses_to_the_deadline() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Answer(s(30))))]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let id = a.id;
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_exceeded"), "a stop condition wins over a child that is ready at the same moment");
    assert_eq!(t0.elapsed(), s(30));
    sleep(s(2)).await;
    assert_eq!(record(&pool, id)["state"], "expired");
}

#[tokio::test(start_paused = true)]
async fn a_cancellation_that_arrives_as_the_child_becomes_ready_stops_the_request_from_being_sent() {
    let slot: Arc<Mutex<Option<Arc<Pool<FakeLaunch>>>>> = Arc::new(Mutex::new(None));
    let hook = slot.clone();
    let scripts = vec![Some(Script::new().on_ready(move || {
        if let Some(p) = hook.lock().unwrap().as_ref() {
            p.cancel(1); // a cancellation arrives at the very moment the child says it is ready
        }
    }))];
    let (pool, log, _t0) = pool_of(limits(1, 1), scripts);
    let pool = Arc::new(pool);
    *slot.lock().unwrap() = Some(pool.clone());
    let t = pool.enqueue("t", n(1), None).unwrap();
    let id = t.id;
    let _keep = t;
    sleep(s(3)).await;
    let ev = events(&log);
    assert_eq!(ev.iter().any(|e| e.starts_with("ready1")), true, "{ev:?}");
    assert_eq!(ev.iter().any(|e| e.starts_with("request")), false, "the request was not sent after the stop: {ev:?}");
    let r = record(&pool, id);
    assert_eq!((r["state"].as_str(), r["reason"].as_str()), (Some("cancelled"), Some("cancelled_while_running")), "{r}");
    assert_eq!(ev.iter().any(|e| e.starts_with("exit1")), true, "the child that was started was ended and confirmed gone: {ev:?}");
}

/// A real clock, not the paused one: under a paused clock the select's deadline branch always ends the wait first, so the check just before the request is sent cannot be reached. Here the hook blocks the thread,
/// so the deadline passes inside the very poll in which the child reports ready.
#[tokio::test]
async fn a_deadline_that_passes_while_the_child_becomes_ready_stops_the_request_from_being_sent() {
    let scripts = vec![Some(Script::new().terminate(Exit::After(Duration::ZERO)).on_ready(|| std::thread::sleep(Duration::from_millis(200))))];
    let (pool, log, _t0) = pool_of(limits(1, 1), scripts);
    let a = pool.enqueue("t", n(1), Some(Duration::from_millis(80))).unwrap();
    let id = a.id;
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_exceeded"));
    sleep(Duration::from_millis(300)).await; // (the child is ended after the caller is told)
    let ev = events(&log);
    assert_eq!(ev.iter().any(|e| e.starts_with("request")), false, "the request was not sent after the deadline: {ev:?}");
    assert_eq!(ev.iter().any(|e| e.starts_with("ready1")), true, "{ev:?}");
    assert_eq!(record(&pool, id)["state"], "expired");
}
#[tokio::test(start_paused = true)]
async fn shutdown_ends_a_warm_idle_child_stops_its_worker_and_refuses_new_jobs() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new())]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    assert_eq!(matches!(bounded(a.result()).await, Outcome::Reply(_)), true);
    let report = bounded(pool.shutdown()).await;
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 });
    let ev = events(&log);
    assert_eq!(&ev[ev.len() - 2..], ["terminate1@0", "exit1@1"], "{ev:?}");
    assert_eq!(pool.enqueue("t", n(2), None).err(), Some(Refusal::Closed));
}

#[tokio::test(start_paused = true)]
async fn shutdown_cancels_what_waits_and_what_runs_and_returns_only_when_the_children_are_gone() {
    let (pool, log, t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Silent))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let b = pool.enqueue("t", n(2), None).unwrap();
    let (aid, bid) = (a.id, b.id);
    let (ha, hb) = (tokio::spawn(a.result()), tokio::spawn(b.result()));
    sleep(s(10)).await;
    let report = bounded(pool.shutdown()).await;
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 });
    assert_eq!(t0.elapsed(), s(11), "terminate at 10, confirmed gone at 11: it returned only then");
    assert_eq!(bounded(ha).await.unwrap(), Outcome::Cancelled);
    assert_eq!(bounded(hb).await.unwrap(), Outcome::Cancelled);
    assert_eq!(events(&log).iter().any(|e| e.starts_with("spawn2")), false, "the queued job never reached a child");
    assert_eq!((record(&pool, aid)["reason"].as_str(), record(&pool, bid)["reason"].as_str()), (Some("cancelled_while_running"), Some("pool_closed")));
}

#[tokio::test(start_paused = true)]
async fn a_child_that_cannot_be_confirmed_gone_at_shutdown_is_reported_and_not_hidden() {
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    let report = bounded(pool.shutdown()).await;
    assert_eq!(report, ShutdownReport { ended: 0, unconfirmed: 1 });
    assert_eq!(pool.enqueue("t", n(2), None).err(), Some(Refusal::Closed));
}

#[tokio::test(start_paused = true)]
async fn completing_the_last_ticket_after_the_pool_is_dropped_does_not_strand_the_worker() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Answer(s(10))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    drop(pool);
    let r = bounded(a.result()).await;
    assert_eq!(matches!(r, Outcome::Reply(_)), true, "the job in flight was not cancelled by the pool going away: {r:?}");
    sleep(s(5)).await;
    let ev = events(&log);
    assert_eq!(ev.iter().any(|e| e == "terminate1@10") && ev.iter().any(|e| e.starts_with("exit1")), true, "the idle child was ended once nobody could use it: {ev:?}");
}

// ======================================================================= T6 review: a failure is told only when the child is known to be gone

#[tokio::test(start_paused = true)]
async fn a_failure_after_the_request_was_sent_is_told_only_when_the_child_is_confirmed_gone() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Drops(s(2))).terminate(Exit::After(s(3))))]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("no_answer"));
    assert_eq!(t0.elapsed(), s(5), "no answer at 2, child confirmed gone at 5: nothing is left running beside whatever the caller does next");
}

#[tokio::test(start_paused = true)]
async fn a_failure_whose_child_cannot_be_confirmed_gone_is_unconfirmed_and_holds_the_worker() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Drops(s(2))).immortal())]);
    let a = pool.enqueue("t", n(1), None).unwrap();
    let id = a.id;
    assert_eq!(bounded(a.result()).await, Outcome::Unconfirmed("no_answer"));
    assert_eq!(t0.elapsed(), s(12), "2 s, then terminate, wait 5, kill, wait 5");
    assert_eq!(record(&pool, id)["state"], "cancel_unconfirmed");
    assert_eq!(pool.status()["retained"][0]["job"], id);
}

// ======================================================================= T6 review: R3 identity, overrides and restart

#[test]
fn an_override_needs_an_operator_and_a_reason_of_sensible_size_and_no_control_characters() {
    assert_eq!(OverrideNote::new("", "x"), Err(NoteError::Operator));
    assert_eq!(OverrideNote::new("   ", "x"), Err(NoteError::Operator));
    assert_eq!(OverrideNote::new(&"a".repeat(65), "x"), Err(NoteError::Operator));
    assert_eq!(OverrideNote::new("a\nb", "x"), Err(NoteError::Operator));
    assert_eq!(OverrideNote::new("asha", ""), Err(NoteError::Reason));
    assert_eq!(OverrideNote::new("asha", &"r".repeat(241)), Err(NoteError::Reason));
    assert_eq!(OverrideNote::new("asha", "line\u{7}bell"), Err(NoteError::Reason));
    let ok = OverrideNote::new("  asha  ", "  gone  ").unwrap();
    assert_eq!((ok.operator.as_str(), ok.reason.as_str()), ("asha", "gone"));
    assert_eq!(OverrideNote::new(&"a".repeat(64), &"r".repeat(240)).is_ok(), true);
}

#[tokio::test(start_paused = true)]
async fn the_status_names_each_workers_child_by_its_process_id_and_launch_number() {
    let scripts = vec![Some(Script::new().req(Req::Silent)), Some(Script::new().req(Req::Silent).immortal())];
    let (pool, _log, _t0) = pool_of(limits(2, 1), scripts);
    let a = pool.enqueue("view", n(1), Some(s(30))).unwrap();
    let b = pool.enqueue("view", n(2), Some(s(30))).unwrap();
    let (aid, bid) = (a.id, b.id);
    sleep(s(1)).await;
    let st = pool.status();
    let workers = st["workers_detail"].as_array().unwrap().clone();
    for w in &workers {
        assert_eq!(w["state"], "running", "{st}");
        assert_eq!(w["pid"].as_u64(), Some(1000 + w["child"].as_u64().unwrap()), "the pid is the child's own: {st}");
        assert_eq!(w["kind"], "view");
    }
    let on = |job: u64| workers.iter().find(|w| w["job"] == job).cloned().expect("a worker runs it");
    assert_eq!(on(aid)["child"].as_u64().unwrap() + on(bid)["child"].as_u64().unwrap(), 3, "two different launches, 1 and 2");
    let (ta, tb) = (tokio::spawn(a.result()), tokio::spawn(b.result()));
    let _ = bounded(async { tokio::join!(ta, tb) }).await;
    sleep(s(20)).await; // deadlines at 30; the first child's exit is confirmed, the immortal one's is not
    let st = pool.status();
    let held = st["retained"].as_array().unwrap();
    assert_eq!(held.len(), 1, "{st}");
    assert_eq!((held[0]["child"].as_u64(), held[0]["pid"].as_u64(), held[0]["origin"].as_str()), (Some(2), Some(1002), Some("this_run")), "{st}");
    let job = held[0]["job"].as_u64().unwrap();
    assert_eq!(record(&pool, job)["pid"], 1002, "the job's own record carries it");
}

#[tokio::test(start_paused = true)]
async fn an_override_records_who_did_it_and_why_with_the_job_and_every_attempt_is_kept() {
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("t", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    assert_eq!(pool.resolve(aid, Resolution::Retry, OverrideNote::new("asha", "asked again").unwrap()), Ok(()));
    sleep(s(11)).await;
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone, OverrideNote::new("  asha  ", "task manager shows pid 1001 gone").unwrap()), Ok(()));
    sleep(s(1)).await;
    let r = record(&pool, aid);
    let o = r["overrides"].as_array().cloned().unwrap_or_default();
    assert_eq!(o.len(), 2, "{r}");
    assert_eq!((o[0]["action"].as_str(), o[0]["operator"].as_str(), o[0]["reason"].as_str()), (Some("retry"), Some("asha"), Some("asked again")));
    assert_eq!((o[1]["action"].as_str(), o[1]["operator"].as_str(), o[1]["reason"].as_str()), (Some("confirmed_gone"), Some("asha"), Some("task manager shows pid 1001 gone")));
    assert_eq!(o[1]["at_unix_ms"].is_u64() && o[1]["at_ms"].is_u64(), true);
    assert_eq!(r["resolution"], "resolved_by_person");
}

fn journal_path(tag: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("athena-bounded-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.join("held.json")
}
fn pool_journaled(path: &std::path::Path, l: Limits, scripts: Vec<Option<Script>>) -> (Pool<FakeLaunch>, Log, Instant) {
    let log: Log = Arc::new(Mutex::new(Vec::new()));
    let t0 = Instant::now();
    let launch = FakeLaunch { scripts: Mutex::new(scripts.into()), log: log.clone(), t0, spawned: Mutex::new(0) };
    (Pool::with_journal("test", launch, l, path.to_path_buf()), log, t0)
}

#[tokio::test(start_paused = true)]
async fn a_held_child_is_written_to_the_journal_and_taken_out_again_when_it_is_freed() {
    let path = journal_path("journal-a");
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("view", json!({"n": 1, "query": "SECRET-QUERY-77"}), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let j: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    assert_eq!((j["version"].as_u64(), j["held"][0]["job"].as_u64(), j["held"][0]["pid"].as_u64(), j["held"][0]["slot"].as_u64()), (Some(1), Some(aid), Some(1001), Some(0)), "the held child is on file: {text}");
    assert_eq!(text.contains("SECRET-QUERY-77"), false, "what was asked is not written down");
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone, note()), Ok(()));
    sleep(s(1)).await;
    let j: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap_or_default()).unwrap_or(Value::Null);
    assert_eq!(j["held"].as_array().map(|h| h.len()), Some(0), "freed: nothing is held on file");
}

#[tokio::test(start_paused = true)]
async fn after_a_restart_a_worker_held_by_the_earlier_run_stays_held_until_a_person_says_the_process_is_gone() {
    let path = journal_path("journal-b");
    let (first, _l, _t) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = first.enqueue("view", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    // the terminal is started again: a new pool reads the same file
    let (pool, log, _t0) = pool_journaled(&path, limits(1, 2), vec![]);
    let st = pool.status();
    let held = st["retained"].as_array().cloned().unwrap_or_default();
    assert_eq!(held.len(), 1, "{st}");
    assert_eq!((held[0]["job"].as_u64(), held[0]["pid"].as_u64(), held[0]["origin"].as_str()), (Some(aid), Some(1001), Some("previous_run")), "{st}");
    assert_eq!(pool.enqueue("view", n(1), None).err(), Some(Refusal::Held { job: aid }), "the one worker is still held");
    assert_eq!(pool.resolve(aid, Resolution::Retry, note()), Err(ResolveError::NoHandle), "there is no process handle to try again with");
    assert_eq!(events(&log).len(), 0, "nothing was started or ended on a guess");
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone, OverrideNote::new("asha", "pid 1001 is not in the task list").unwrap()), Ok(()));
    sleep(s(1)).await;
    assert_eq!(pool.status()["retained"].as_array().map(|h| h.len()), Some(0));
    let r = record(&pool, aid);
    assert_eq!((r["resolution"].as_str(), r["overrides"][0]["operator"].as_str()), (Some("resolved_by_person"), Some("asha")), "{r}");
    let b = pool.enqueue("view", n(2), None).unwrap();
    assert_eq!(b.id > aid, true, "job numbers go on from the earlier run: {} after {aid}", b.id);
    assert_eq!(matches!(bounded(b.result()).await, Outcome::Reply(_)), true);
}

// CHANGED ON PURPOSE in T6 review 2 (item 4): this test used to assert that a pool with an unreadable journal "works" and reports the error. That is recovery failing open: a corrupt file is not evidence that nothing
// is held. The pool now reports the reason and refuses work until a person acknowledges it (see `an_unreadable_journal_blocks_admission_until_a_person_acknowledges_it_and_the_file_is_kept`).
#[tokio::test(start_paused = true)]
async fn an_unreadable_journal_is_reported_and_the_pool_does_not_take_work() {
    let path = journal_path("journal-c");
    std::fs::write(&path, "this is not json").unwrap();
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![]);
    assert_eq!(pool.enqueue("view", n(1), None).err(), Some(Refusal::Unrecovered), "the pool does not take work on a guess");
    assert_eq!(pool.status()["recovery"]["blocked"].is_string(), true, "{}", pool.status());
}
// ======================================================================= T6 review: R4 operation kinds

#[tokio::test(start_paused = true)]
async fn each_job_carries_its_operation_kind_and_the_status_totals_waits_and_runs_per_kind_without_any_payload() {
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Answer(s(40))).req(Req::Answer(s(10))))]);
    let secret = json!({"n": 1, "query": "SECRET-QUERY-77"});
    let a = pool.enqueue("view", secret.clone(), None).unwrap();
    let b = pool.enqueue("backtest", secret.clone(), None).unwrap();
    assert_eq!(pool.enqueue("view", secret, None).err(), Some(Refusal::QueueFull { capacity: 1 }));
    let queued = pool.status()["queued"].clone();
    assert_eq!(queued[0]["kind"], "backtest", "a waiting job says what it is: {queued}");
    let (aid, bid) = (a.id, b.id);
    let _ = bounded(async { tokio::join!(a.result(), b.result()) }).await;
    let st = pool.status();
    assert_eq!((record(&pool, aid)["kind"].as_str(), record(&pool, bid)["kind"].as_str()), (Some("view"), Some("backtest")));
    let (v, bt) = (&st["kinds"]["view"], &st["kinds"]["backtest"]);
    assert_eq!((v["submitted"].as_u64(), v["refused_queue_full"].as_u64(), v["done"].as_u64()), (Some(1), Some(1), Some(1)), "{st}");
    assert_eq!((v["wait_ms_total"].as_u64(), v["run_ms_total"].as_u64(), v["run_ms_max"].as_u64()), (Some(0), Some(40_000), Some(40_000)), "{st}");
    assert_eq!((bt["submitted"].as_u64(), bt["done"].as_u64(), bt["wait_ms_total"].as_u64(), bt["wait_ms_max"].as_u64(), bt["run_ms_total"].as_u64()), (Some(1), Some(1), Some(40_000), Some(40_000), Some(10_000)), "{st}");
    assert_eq!(st.to_string().contains("SECRET-QUERY-77"), false, "no payload reaches the status");
}

#[tokio::test(start_paused = true)]
async fn an_expired_a_cancelled_and_an_unconfirmed_job_are_counted_under_their_own_kind() {
    let (pool, _log, _t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("page", n(1), Some(s(30))).unwrap();
    let b = pool.enqueue("chart_bars", n(2), None).unwrap();
    drop(b);
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    let st = pool.status();
    assert_eq!(st["kinds"]["chart_bars"]["cancelled"], 1, "{st}");
    assert_eq!((st["kinds"]["page"]["cancel_unconfirmed"].as_u64(), st["kinds"]["page"]["submitted"].as_u64()), (Some(1), Some(1)), "{st}");
}

// ======================================================================= T6 review: R2 one deadline and bounded admission for every path

#[tokio::test(start_paused = true)]
async fn a_budget_is_one_deadline_that_every_attempt_draws_on() {
    let b = Budget::new(s(10));
    assert_eq!((b.remaining(), b.expired()), (s(10), false));
    sleep(s(4)).await;
    assert_eq!(b.remaining(), s(6));
    sleep(s(6)).await;
    assert_eq!((b.remaining(), b.expired()), (Duration::ZERO, true));
}

#[tokio::test(start_paused = true)]
async fn admission_bounds_what_runs_and_what_waits_and_the_wait_counts_against_the_deadline() {
    let adm = Arc::new(Admission::new("t", 1, 1));
    let first = bounded(adm.admit("page", &Budget::new(s(100)))).await.expect("a free place");
    let waiter = tokio::spawn({ let adm = adm.clone(); async move { adm.admit("valuation", &Budget::new(s(10))).await.map(|_| ()) } });
    sleep(s(1)).await;
    let refused = bounded(adm.admit("page", &Budget::new(s(10)))).await.map(|_| ());
    assert_eq!(refused, Err(AdmitError::Full { capacity: 1 }), "the waiting place is taken: refused at once");
    assert_eq!(bounded(waiter).await.unwrap(), Err(AdmitError::Expired), "its 10 s ran out while it waited");
    drop(first);
    let again = bounded(adm.admit("page", &Budget::new(s(5)))).await;
    assert_eq!(again.is_ok(), true, "both the running place and the waiting place are free again");
    let st = adm.status();
    assert_eq!((st["kinds"]["valuation"]["expired"].as_u64(), st["kinds"]["valuation"]["wait_ms_total"].as_u64()), (Some(1), Some(10_000)), "{st}");
    assert_eq!((st["kinds"]["page"]["admitted"].as_u64(), st["kinds"]["page"]["refused_full"].as_u64()), (Some(2), Some(1)), "{st}");
}

#[tokio::test(start_paused = true)]
async fn a_waiter_is_admitted_when_a_place_frees_and_runs_on_what_is_left_of_its_budget() {
    let adm = Arc::new(Admission::new("t", 1, 2));
    let first = bounded(adm.admit("page", &Budget::new(s(100)))).await.expect("a free place");
    let budget = Budget::new(s(30));
    let waiter = tokio::spawn({ let adm = adm.clone(); async move { adm.admit("page", &budget).await.map(|_| budget.remaining()) } });
    sleep(s(12)).await;
    drop(first);
    assert_eq!(bounded(waiter).await.unwrap(), Ok(s(18)), "30 s in all, 12 spent waiting");
}

// ======================================================================= T6 review 2: R2 ticket lifetime, R3 cleanup of direct commands and waiting places, R4 recovery

fn py() -> Option<String> {
    ["../.venv/Scripts/python.exe", "../.venv/bin/python"].into_iter().find(|p| std::path::Path::new(p).exists()).map(String::from)
}

#[tokio::test(start_paused = true)]
async fn dropping_the_ticket_of_a_running_job_lets_it_finish_and_discards_the_result() {
    let (pool, log, _t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Answer(s(100))).req(Req::Answer(s(5))))]);
    let a = pool.enqueue("view", n(1), None).unwrap();
    let b = pool.enqueue("view", n(2), None).unwrap();
    let aid = a.id;
    let hb = tokio::spawn(b.result());
    sleep(s(10)).await;
    drop(a); // the caller went away while its read-only job runs
    sleep(s(100)).await;
    let ev = events(&log);
    assert_eq!(ev.iter().any(|e| e.starts_with("terminate") || e.starts_with("kill")), false, "the running job was not stopped: {ev:?}");
    let r = record(&pool, aid);
    assert_eq!((r["state"].as_str(), r["abandoned"].as_bool()), (Some("done"), Some(true)), "it ran to the end and is marked abandoned: {r}");
    assert_eq!(matches!(bounded(hb).await.unwrap(), Outcome::Reply(_)), true, "the job behind it ran next");
    assert_eq!(ev.iter().filter(|e| e.starts_with("spawn")).count(), 1, "one warm child served both: {ev:?}");
}

// ---- admission: a waiting place is given back when the waiter goes away ----

#[tokio::test(start_paused = true)]
async fn waiters_that_are_cancelled_give_their_waiting_place_back_so_later_requests_can_still_enter() {
    let adm = Arc::new(Admission::new("t", 1, 2));
    let held = bounded(adm.admit("page", &Budget::new(s(1000)))).await.ok().expect("a free place");
    for _ in 0..6 {
        let waiter = tokio::spawn({ let adm = adm.clone(); async move { adm.admit("page", &Budget::new(s(1000))).await.map(|_| ()) } });
        sleep(s(1)).await; // it is waiting
        waiter.abort(); // its caller went away
        sleep(s(1)).await;
    }
    assert_eq!(adm.status()["waiting"], 0, "every cancelled waiter gave its place back: {}", adm.status());
    assert_eq!(adm.status()["kinds"]["page"]["cancelled"], 6, "and each is counted as cancelled: {}", adm.status());
    let later = tokio::spawn({ let adm = adm.clone(); async move { adm.admit("page", &Budget::new(s(1000))).await.map(|_| ()) } });
    sleep(s(1)).await;
    drop(held);
    assert_eq!(bounded(later).await.unwrap(), Ok(()), "a later request could still wait for a place, and got it");
}

// ---- direct commands: a child owned apart from the caller, its place kept until its exit is confirmed ----

#[derive(Clone)]
struct DScript {
    /// When it ends by itself (None: it never does).
    finish_after: Option<Duration>,
    /// It is confirmed gone only after this many kills (0: never) and `confirm_delay` after the last.
    confirm_after_kills: u32,
    confirm_delay: Duration,
}
impl DScript {
    fn finishes_after(d: Duration) -> DScript {
        DScript { finish_after: Some(d), confirm_after_kills: 1, confirm_delay: Duration::ZERO }
    }
    fn overruns_and_exits_after_a_kill(delay: Duration) -> DScript {
        DScript { finish_after: None, confirm_after_kills: 1, confirm_delay: delay }
    }
    fn immortal() -> DScript {
        DScript { finish_after: None, confirm_after_kills: 0, confirm_delay: Duration::ZERO }
    }
    fn exits_after_kills(kills: u32) -> DScript {
        DScript { finish_after: None, confirm_after_kills: kills, confirm_delay: s(1) }
    }
}
struct FakeDirect {
    script: DScript,
    kills: u32,
    gone: bool,
    log: Log,
    t0: Instant,
    id: usize,
}
impl FakeDirect {
    fn new(script: DScript, log: &Log, t0: Instant, id: usize) -> FakeDirect {
        FakeDirect { script, kills: 0, gone: false, log: log.clone(), t0, id }
    }
    fn note(&self, what: &str) {
        self.log.lock().unwrap().push(format!("{what}{}@{}", self.id, self.t0.elapsed().as_secs()));
    }
}
impl DirectChild for FakeDirect {
    async fn finished(&mut self) -> DirectOutput {
        match self.script.finish_after {
            Some(d) => {
                sleep(d).await;
                self.gone = true;
                self.note("exit");
                DirectOutput { success: true, code: Some(0), stdout: b"out".to_vec() }
            }
            None => std::future::pending::<DirectOutput>().await,
        }
    }
    fn kill(&mut self) {
        self.kills += 1;
        self.note("kill");
    }
    async fn wait_exit(&mut self, within: Duration) -> bool {
        if self.gone {
            return true;
        }
        if self.script.confirm_after_kills > 0 && self.kills >= self.script.confirm_after_kills && self.script.confirm_delay <= within {
            sleep(self.script.confirm_delay).await;
            self.gone = true;
            self.note("exit");
            return true;
        }
        sleep(within).await;
        false
    }
    fn pid(&self) -> Option<u32> {
        Some(2000 + self.id as u32)
    }
}
fn held_of(adm: &Admission) -> Vec<Value> {
    adm.status()["held"].as_array().cloned().unwrap_or_default()
}

#[tokio::test(start_paused = true)]
async fn a_direct_command_that_finishes_returns_its_output_and_frees_its_place() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let r = bounded(adm.run_direct(permit, FakeDirect::new(DScript::finishes_after(s(4)), &log, t0, 1), "page", &Budget::new(s(100)))).await;
    assert_eq!(r, Ok(DirectOutput { success: true, code: Some(0), stdout: b"out".to_vec() }));
    assert_eq!(t0.elapsed(), s(4));
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true, "its place is free");
}

#[tokio::test(start_paused = true)]
async fn a_direct_command_past_its_deadline_is_reported_at_the_deadline_but_keeps_its_place_until_its_exit_is_confirmed() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let r = bounded(adm.run_direct(permit, FakeDirect::new(DScript::overruns_and_exits_after_a_kill(s(3)), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    assert_eq!(r, Err(DirectError::Timeout));
    assert_eq!(t0.elapsed(), s(10), "told at its deadline, not after the cleanup");
    sleep(s(1)).await; // 11: killed at 10, gone at 13
    let early = bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ());
    assert_eq!(early, Err(AdmitError::Full { capacity: 0 }), "the place is still the command's: it is not confirmed gone yet");
    sleep(s(3)).await; // 15
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true, "free once the exit was confirmed");
    assert_eq!(events(&log), vec!["kill1@10", "exit1@13"]);
}

#[tokio::test(start_paused = true)]
async fn a_direct_command_that_cannot_be_confirmed_gone_keeps_its_place_until_a_person_resolves_it() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let r = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    assert_eq!(r, Err(DirectError::Timeout));
    sleep(s(12)).await; // kill at 10, kill again at 15, unconfirmed at 20
    let held = held_of(&adm);
    assert_eq!(held.len(), 1, "{}", adm.status());
    assert_eq!((held[0]["kind"].as_str(), held[0]["pid"].as_u64(), held[0]["origin"].as_str()), (Some("page"), Some(2001), Some("this_run")), "{}", adm.status());
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Held), "the only place is held by a command that could not be confirmed stopped");
    let id = held[0]["id"].as_u64().unwrap();
    assert_eq!(adm.resolve(id, Resolution::ConfirmedGone, OverrideNote::new("asha", "pid 2001 is not in the task list").unwrap()), Ok(()));
    sleep(s(1)).await;
    assert_eq!(held_of(&adm).len(), 0);
    let resolved = adm.status()["resolved"].as_array().cloned().unwrap_or_default();
    assert_eq!((resolved[0]["resolution"].as_str(), resolved[0]["overrides"][0]["operator"].as_str(), resolved[0]["overrides"][0]["reason"].as_str()), (Some("resolved_by_person"), Some("asha"), Some("pid 2001 is not in the task list")), "{}", adm.status());
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true, "the place is free again");
}

#[tokio::test(start_paused = true)]
async fn a_person_can_ask_for_another_attempt_to_end_a_held_direct_command() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::exits_after_kills(3), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(12)).await; // two kills so far, still unconfirmed
    let id = held_of(&adm)[0]["id"].as_u64().unwrap();
    assert_eq!(adm.resolve(id, Resolution::Retry, OverrideNote::new("asha", "try again").unwrap()), Ok(()));
    sleep(s(3)).await;
    assert_eq!(held_of(&adm).len(), 0, "the third attempt was confirmed: {}", adm.status());
    let resolved = adm.status()["resolved"].as_array().cloned().unwrap_or_default();
    assert_eq!((resolved[0]["resolution"].as_str(), resolved[0]["overrides"][0]["action"].as_str()), (Some("confirmed_after_retry"), Some("retry")), "{}", adm.status());
    assert_eq!(adm.resolve(id, Resolution::Retry, note()), Err(ResolveError::NotRetained), "a freed one cannot be resolved again");
}

#[tokio::test(start_paused = true)]
async fn a_caller_that_goes_away_does_not_end_its_direct_command_and_the_place_stays_until_it_is_gone() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let task = tokio::spawn({ let (adm, log) = (adm.clone(), log.clone()); async move { adm.run_direct(permit, FakeDirect::new(DScript::finishes_after(s(5)), &log, t0, 1), "page", &Budget::new(s(100))).await.map(|_| ()) } });
    sleep(s(2)).await;
    task.abort(); // the caller was dropped (its client went away)
    sleep(s(1)).await; // 3
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Full { capacity: 0 }), "the command is still running and still holds its place");
    sleep(s(3)).await; // 7: it finished at 5
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true);
    assert_eq!(events(&log), vec!["exit1@5"], "nothing was killed: a read-only command is let finish");
}

#[tokio::test]
async fn a_real_command_that_overruns_is_ended_and_really_gone_before_its_place_is_free() {
    let Some(py) = py() else { return };
    let beat = std::env::temp_dir().join(format!("athena-bounded-beat-{}.txt", std::process::id()));
    let _ = std::fs::remove_file(&beat);
    let code = format!("import time\nwhile True:\n    open(r'{}', 'a').write('x')\n    time.sleep(0.05)\n", beat.display());
    let adm = Arc::new(Admission::new("t", 1, 0));
    let permit = adm.admit("page", &Budget::new(Duration::from_secs(5))).await.ok().expect("a place");
    let mut cmd = Command::new(py);
    cmd.args(["-c", &code]);
    let r = adm.run_command(permit, cmd, "page", &Budget::new(Duration::from_millis(600))).await;
    assert_eq!(r.map(|_| ()), Err(DirectError::Timeout));
    let mut freed = false;
    for _ in 0..100 {
        if adm.admit("page", &Budget::new(Duration::from_millis(10))).await.is_ok() {
            freed = true;
            break;
        }
        sleep(Duration::from_millis(50)).await;
    }
    assert_eq!(freed, true, "the place came back after the exit was confirmed: {}", adm.status());
    let size = std::fs::metadata(&beat).map(|m| m.len()).unwrap_or(0);
    sleep(Duration::from_millis(400)).await;
    assert_eq!(std::fs::metadata(&beat).map(|m| m.len()).unwrap_or(0), size, "nothing is writing any more: the process is gone");
}

// ---- recovery: what a restart knows, and what it will not guess ----

fn journal_json(path: &std::path::Path) -> Value {
    serde_json::from_str(&std::fs::read_to_string(path).unwrap_or_default()).unwrap_or(Value::Null)
}
fn held_jobs(path: &std::path::Path) -> Vec<u64> {
    journal_json(path)["held"].as_array().map(|h| h.iter().filter_map(|e| e["job"].as_u64()).collect()).unwrap_or_default()
}

#[tokio::test(start_paused = true)]
async fn an_idle_child_that_cannot_be_confirmed_gone_at_shutdown_is_journalled_and_the_restart_is_quarantined() {
    let path = journal_path("journal-idle");
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new().immortal())]);
    let a = pool.enqueue("view", n(1), None).unwrap();
    assert_eq!(matches!(bounded(a.result()).await, Outcome::Reply(_)), true, "the job was answered and its child kept warm");
    assert_eq!(bounded(pool.shutdown()).await, ShutdownReport { ended: 0, unconfirmed: 1 });
    let j = journal_json(&path);
    assert_eq!((j["held"].as_array().map(|h| h.len()), j["held"][0]["kind"].as_str(), j["held"][0]["pid"].as_u64()), (Some(1), Some("idle_child"), Some(1001)), "the unconfirmed idle child is on file: {j}");
    // the terminal is started again
    let (pool2, log2, _t) = pool_journaled(&path, limits(1, 1), vec![]);
    let job = held_jobs(&path)[0];
    assert_eq!(pool2.enqueue("view", n(2), None).err(), Some(Refusal::Held { job }), "no new work is put beside a process that may still be running");
    assert_eq!(pool2.resolve(job, Resolution::ConfirmedGone, note()), Ok(()));
    let b = pool2.enqueue("view", n(2), None).expect("freed by a person");
    assert_eq!(matches!(bounded(b.result()).await, Outcome::Reply(_)), true);
    assert_eq!(events(&log2).iter().filter(|e| e.starts_with("spawn")).count(), 1);
    assert_eq!(held_jobs(&path).len(), 0);
}

#[tokio::test(start_paused = true)]
async fn an_unreadable_journal_blocks_admission_until_a_person_acknowledges_it_and_the_file_is_kept() {
    let path = journal_path("journal-corrupt");
    std::fs::write(&path, "this is not json").unwrap();
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![]);
    assert_eq!(pool.enqueue("view", n(1), None).err(), Some(Refusal::Unrecovered), "recovery state that cannot be read is not read as nothing held");
    assert_eq!(pool.status()["recovery"]["blocked"].is_string(), true, "{}", pool.status());
    assert_eq!(pool.resolve(0, Resolution::AcknowledgeRecovery, OverrideNote::new("asha", "the file is junk; checked the task list by hand").unwrap()), Ok(()));
    assert_eq!(pool.status()["recovery"]["acknowledgements"][0]["operator"], "asha", "{}", pool.status());
    let kept = std::fs::read_dir(path.parent().unwrap()).unwrap().filter_map(|e| e.ok()).any(|e| e.path() != path && std::fs::read_to_string(e.path()).map(|t| t == "this is not json").unwrap_or(false));
    assert_eq!(kept, true, "the unreadable file was kept beside the new one");
    let a = pool.enqueue("view", n(1), None).expect("admitted after the acknowledgement");
    assert_eq!(matches!(bounded(a.result()).await, Outcome::Reply(_)), true);
    assert_eq!(pool.resolve(0, Resolution::AcknowledgeRecovery, note()), Err(ResolveError::NotBlocked), "nothing is blocked now");
}

#[tokio::test(start_paused = true)]
async fn reducing_the_worker_count_keeps_the_unresolved_entries_that_no_longer_have_a_slot() {
    let path = journal_path("journal-fewer");
    let (first, _l, _t) = pool_journaled(&path, limits(2, 1), vec![Some(Script::new().req(Req::Silent).immortal()), Some(Script::new().req(Req::Silent).immortal())]);
    let a = first.enqueue("view", n(1), Some(s(30))).unwrap();
    let b = first.enqueue("view", n(2), Some(s(30))).unwrap();
    let (aid, bid) = (a.id, b.id);
    let _ = bounded(async { tokio::join!(a.result(), b.result()) }).await;
    sleep(s(15)).await;
    assert_eq!(held_jobs(&path).len(), 2, "both held children are on file");
    // restarted with one worker: two unresolved entries, one slot
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![]);
    assert_eq!(pool.status()["retained"].as_array().map(|r| r.len()), Some(2), "both are kept and shown, one of them with no slot: {}", pool.status());
    assert_eq!(matches!(pool.enqueue("view", n(1), None).err(), Some(Refusal::Held { .. })), true);
    assert_eq!(pool.resolve(bid, Resolution::Retry, note()), Err(ResolveError::NoHandle), "an entry with no worker has no process handle either");
    assert_eq!(pool.resolve(aid, Resolution::Retry, note()), Err(ResolveError::NoHandle));
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone, note()), Ok(()));
    assert_eq!(held_jobs(&path), vec![bid], "resolving one leaves the other on file");
    assert_eq!(matches!(pool.enqueue("view", n(1), None).err(), Some(Refusal::Held { .. })), true, "the other is still unresolved");
    assert_eq!(pool.resolve(bid, Resolution::ConfirmedGone, note()), Ok(()));
    assert_eq!(pool.enqueue("view", n(1), None).is_ok(), true, "free once both are resolved");
    assert_eq!(held_jobs(&path).len(), 0);
}

#[tokio::test(start_paused = true)]
async fn a_failed_journal_write_is_reported_and_the_next_write_publishes_everything_held() {
    let path = journal_path("journal-fail");
    let (pool, _log, _t0) = pool_journaled(&path, limits(2, 1), vec![Some(Script::new().req(Req::Silent).immortal()), Some(Script::new().req(Req::Silent).immortal())]);
    pool.fail_next_journal_writes(1);
    let a = pool.enqueue("view", n(1), Some(s(30))).unwrap();
    let b = pool.enqueue("view", n(2), Some(s(60))).unwrap();
    let (aid, bid) = (a.id, b.id);
    let (ha, hb) = (tokio::spawn(a.result()), tokio::spawn(b.result()));
    sleep(s(45)).await; // the first is held at 40: its write failed
    assert_eq!(pool.status()["journal"]["error"].is_string(), true, "the failure is shown: {}", pool.status());
    sleep(s(40)).await; // the second is held at 70: its write works and carries both
    assert_eq!(pool.status()["journal"]["error"].is_null(), true, "{}", pool.status());
    let mut jobs = held_jobs(&path);
    jobs.sort();
    assert_eq!(jobs, vec![aid, bid], "the earlier entry, whose own write failed, is on file too");
    let _ = (ha.await, hb.await);
}

#[test]
fn concurrent_journal_writes_never_leave_older_state_over_newer_state() {
    use std::sync::atomic::AtomicBool;
    let path = journal_path("journal-race");
    let rec = Arc::new(Recovery::new(Some(path.clone())));
    let state: Arc<Mutex<Vec<HeldEntry>>> = Arc::new(Mutex::new(Vec::new()));
    let entry = HeldEntry { slot: Some(0), job: 7, kind: "view".into(), pid: Some(1), child: Some(1), since_unix_ms: 1 };
    let (entered_tx, entered_rx) = std::sync::mpsc::channel::<()>();
    let (release_tx, release_rx) = std::sync::mpsc::channel::<()>();
    let (first, release_rx) = (Arc::new(AtomicBool::new(true)), Arc::new(Mutex::new(release_rx)));
    let entered_tx = Mutex::new(entered_tx);
    // the first write stops after its snapshot was taken and before the file is written
    *rec.journal.as_ref().unwrap().before_write.lock().unwrap() = Some(Arc::new(move || {
        if first.swap(false, Ordering::SeqCst) {
            entered_tx.lock().unwrap().send(()).ok();
            release_rx.lock().unwrap().recv().ok();
        }
    }));
    let a = std::thread::spawn({
        let (rec, state, entry) = (rec.clone(), state.clone(), entry.clone());
        move || {
            *state.lock().unwrap() = vec![entry]; // the worker is held ...
            rec.persist("t", || state.lock().unwrap().clone());
        }
    });
    entered_rx.recv().unwrap(); // ... and its write is under way with the snapshot [held]
    let b = std::thread::spawn({
        let (rec, state) = (rec.clone(), state.clone());
        move || {
            *state.lock().unwrap() = Vec::new(); // ... and then it is freed, and that is written too
            rec.persist("t", || state.lock().unwrap().clone());
        }
    });
    std::thread::sleep(Duration::from_millis(300)); // (if the writes are not serialised, the second one has finished by now)
    release_tx.send(()).unwrap();
    a.join().unwrap();
    b.join().unwrap();
    assert_eq!(held_jobs(&path), Vec::<u64>::new(), "the later state (nothing held) is what is on file, not the earlier one");
}

#[test]
fn every_journal_write_goes_to_a_temporary_file_of_its_own_and_leaves_none_behind() {
    let path = journal_path("journal-temp");
    let j = Journal::new(path.clone());
    assert_eq!(j.temp_path() != j.temp_path(), true, "two writes never share a temporary name");
    j.store("t", &[]).unwrap();
    j.store("t", &[]).unwrap();
    let names: Vec<String> = std::fs::read_dir(path.parent().unwrap()).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    assert_eq!(names, vec!["held.json".to_string()], "no temporary file is left behind: {names:?}");
}

#[test]
fn a_journal_entry_without_a_slot_is_read_back_and_written_again() {
    let path = journal_path("journal-slotless");
    let j = Journal::new(path.clone());
    let e = HeldEntry { slot: None, job: 3, kind: "idle_child".into(), pid: Some(9), child: Some(2), since_unix_ms: 5 };
    j.store("t", &[e.clone()]).unwrap();
    assert_eq!(j.load(), Ok(vec![e]));
}

// ---- direct-command restart recovery ----

#[tokio::test(start_paused = true)]
async fn a_direct_command_held_at_a_restart_keeps_its_place_held_until_a_person_resolves_it() {
    let path = journal_path("journal-direct");
    let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(12)).await;
    let j = journal_json(&path);
    assert_eq!((j["held"].as_array().map(|h| h.len()), j["held"][0]["kind"].as_str(), j["held"][0]["pid"].as_u64()), (Some(1), Some("page"), Some(2001)), "{j}");
    // the terminal is started again
    let again = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    let one = bounded(again.admit("page", &Budget::new(s(1)))).await.ok().expect("one of the two places is still free");
    assert_eq!(bounded(again.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Full { capacity: 0 }), "the other is withheld for the command that may still be running");
    drop(one);
    let id = held_jobs(&path)[0];
    assert_eq!(again.resolve(id, Resolution::Retry, note()), Err(ResolveError::NoHandle));
    assert_eq!(again.resolve(id, Resolution::ConfirmedGone, note()), Ok(()));
    let (a, b) = (bounded(again.admit("page", &Budget::new(s(1)))).await, bounded(again.admit("page", &Budget::new(s(1)))).await);
    assert_eq!((a.is_ok(), b.is_ok()), (true, true), "both places are back");
    assert_eq!(held_jobs(&path).len(), 0);
}

#[tokio::test(start_paused = true)]
async fn an_unreadable_journal_blocks_an_admission_until_acknowledged() {
    let path = journal_path("journal-direct-corrupt");
    std::fs::write(&path, "{ not json").unwrap();
    let adm = Arc::new(Admission::with_journal("t", 2, 2, path));
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Held));
    assert_eq!(adm.resolve(0, Resolution::AcknowledgeRecovery, note()), Ok(()));
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true);
}

#[tokio::test(start_paused = true)]
async fn an_idle_child_held_at_shutdown_can_be_resolved_in_the_same_run_without_a_worker_to_ask() {
    let path = journal_path("journal-idle-same");
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new().immortal())]);
    let a = pool.enqueue("view", n(1), None).unwrap();
    let _ = bounded(a.result()).await;
    assert_eq!(bounded(pool.shutdown()).await.unconfirmed, 1);
    let job = held_jobs(&path)[0];
    assert_eq!(pool.resolve(job, Resolution::Retry, note()), Err(ResolveError::NoHandle), "its handle went with its worker");
    assert_eq!(pool.resolve(job, Resolution::ConfirmedGone, OverrideNote::new("asha", "checked").unwrap()), Ok(()));
    assert_eq!(held_jobs(&path).len(), 0, "freed at once and off the file");
    assert_eq!(record(&pool, job)["resolution"], "resolved_by_person");
    assert_eq!(record(&pool, job)["overrides"][0]["operator"], "asha");
}

#[test]
fn a_journal_write_that_fails_leaves_no_temporary_file_behind() {
    let dir = std::env::temp_dir().join(format!("athena-bounded-journal-dir-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let path = dir.join("held.json");
    std::fs::create_dir_all(&path).unwrap(); // a directory where the file should go: the move into place fails
    let j = Journal::new(path.clone());
    assert_eq!(j.store("t", &[]).is_err(), true);
    let names: Vec<String> = std::fs::read_dir(&dir).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    assert_eq!(names, vec!["held.json".to_string()], "no temporary file is left behind: {names:?}");
}
// ======================================================================= T6 review 3: shutdown of direct owners, resizing, expired requests, ordinary teardown, undurable journal

fn journal_with(path: &std::path::Path, jobs: &[(u64, Option<usize>)]) {
    let entries: Vec<HeldEntry> = jobs.iter().map(|(job, slot)| HeldEntry { slot: *slot, job: *job, kind: "page".into(), pid: Some(9000 + *job as u32), child: Some(*job), since_unix_ms: 1 }).collect();
    Journal::new(path.to_path_buf()).store("t", &entries).unwrap();
}
const ORDERS: [[usize; 3]; 6] = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]];

// ---- 1. direct owners are part of shutdown ----

#[tokio::test(start_paused = true)]
async fn shutdown_ends_a_running_direct_command_confirms_it_gone_and_tells_its_caller() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let caller = tokio::spawn({ let (adm, log) = (adm.clone(), log.clone()); async move { adm.run_direct(permit, FakeDirect::new(DScript::overruns_and_exits_after_a_kill(s(1)), &log, t0, 1), "page", &Budget::new(s(100))).await } });
    sleep(s(5)).await;
    let report = bounded(PoolView::shutdown(&*adm)).await;
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 }, "the direct child was ended and confirmed gone, and the report says so");
    assert_eq!(events(&log), vec!["kill1@5", "exit1@6"], "it was killed at shutdown and not left to run");
    assert_eq!(bounded(caller).await.unwrap(), Err(DirectError::Shutdown), "its caller is told the terminal is shutting down");
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Closed), "nothing new is admitted");
}

#[tokio::test(start_paused = true)]
async fn shutdown_journals_a_direct_command_that_cannot_be_confirmed_gone_and_reports_it_unconfirmed() {
    let path = journal_path("adm-shutdown-immortal");
    let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let caller = tokio::spawn({ let (adm, log) = (adm.clone(), log.clone()); async move { adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(100))).await } });
    sleep(s(5)).await;
    let report = bounded(PoolView::shutdown(&*adm)).await;
    assert_eq!(report, ShutdownReport { ended: 0, unconfirmed: 1 });
    let j = journal_json(&path);
    assert_eq!((j["held"].as_array().map(|h| h.len()), j["held"][0]["kind"].as_str(), j["held"][0]["pid"].as_u64()), (Some(1), Some("page"), Some(2001)), "the unresolved child is on file before shutdown completes: {j}");
    assert_eq!(bounded(caller).await.unwrap(), Err(DirectError::Shutdown));
    // the terminal is started again: the place is withheld for it
    let again = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    let one = bounded(again.admit("page", &Budget::new(s(1)))).await.ok().expect("one of two places is free");
    assert_eq!(bounded(again.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Full { capacity: 0 }), "the other is withheld for the child that may still be running");
    drop(one);
    // in this run too its owner is gone: there is nothing left to ask to try again, and only a person can free it
    let id = held_jobs(&path)[0];
    assert_eq!(adm.resolve(id, Resolution::Retry, note()), Err(ResolveError::NoHandle));
    assert_eq!(adm.resolve(id, Resolution::ConfirmedGone, note()), Ok(()));
    assert_eq!(held_jobs(&path).len(), 0, "freed at once and off the file");
}

#[tokio::test(start_paused = true)]
async fn shutdown_tries_a_held_direct_command_once_more_and_takes_it_off_the_file_when_it_is_gone() {
    let path = journal_path("adm-shutdown-held");
    let adm = Arc::new(Admission::with_journal("t", 1, 0, path.clone()));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::exits_after_kills(3), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(11)).await; // (the call returned at 10) killed at 10 and 15, unconfirmed at 20: held and on file, 21 now, before the recheck at 35
    assert_eq!(held_jobs(&path).len(), 1, "{}", adm.status());
    let report = bounded(PoolView::shutdown(&*adm)).await;
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 }, "the last attempt, made at shutdown, was confirmed");
    assert_eq!(held_jobs(&path).len(), 0, "and the file says nothing is held");
}

#[tokio::test(start_paused = true)]
async fn a_command_handed_over_after_shutdown_began_is_ended_and_not_left_running() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    assert_eq!(bounded(PoolView::shutdown(&*adm)).await, ShutdownReport::default(), "nothing was running yet");
    let r = bounded(adm.run_direct(permit, FakeDirect::new(DScript::finishes_after(s(4)), &log, t0, 1), "page", &Budget::new(s(100)))).await;
    assert_eq!(r, Err(DirectError::Shutdown));
    assert_eq!(events(&log).first().map(String::as_str), Some("kill1@0"), "it was killed at once: {:?}", events(&log));
    assert_eq!(events(&log).iter().any(|e| e.starts_with("exit1@4")), false, "it did not run to its end");
}

#[tokio::test(start_paused = true)]
async fn the_registry_shutdown_covers_the_direct_owners_of_an_admission() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let registry = WorkRegistry::new();
    registry.register(adm.view());
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let caller = tokio::spawn({ let (adm, log) = (adm.clone(), log.clone()); async move { adm.run_direct(permit, FakeDirect::new(DScript::overruns_and_exits_after_a_kill(s(1)), &log, t0, 1), "page", &Budget::new(s(100))).await } });
    sleep(s(2)).await;
    assert_eq!(bounded(registry.shutdown_all()).await, ShutdownReport { ended: 1, unconfirmed: 0 });
    let _ = caller.await;
}

// ---- 2. resizing: reservations follow the unresolved entries, in any resolution order ----

#[tokio::test(start_paused = true)]
async fn resolving_restored_direct_entries_in_any_order_never_frees_more_places_than_are_left() {
    for (i, order) in ORDERS.into_iter().enumerate() {
        let path = journal_path(&format!("rebalance-adm-{i}"));
        journal_with(&path, &[(1, None), (2, None), (3, None)]);
        let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
        assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Held), "{order:?}: three unresolved children, two places");
        let ids = [1u64, 2, 3];
        for k in 0..2 {
            assert_eq!(adm.resolve(ids[order[k]], Resolution::ConfirmedGone, note()), Ok(()), "{order:?}");
        }
        let first = bounded(adm.admit("page", &Budget::new(s(1)))).await;
        assert_eq!(first.is_ok(), true, "{order:?}: one place is left");
        assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Full { capacity: 0 }), "{order:?}: the other place is still withheld for the child that is unresolved");
        drop(first);
        assert_eq!(adm.resolve(ids[order[2]], Resolution::ConfirmedGone, note()), Ok(()), "{order:?}");
        let (a, b) = (bounded(adm.admit("page", &Budget::new(s(1)))).await, bounded(adm.admit("page", &Budget::new(s(1)))).await);
        assert_eq!((a.is_ok(), b.is_ok()), (true, true), "{order:?}: both places are back once all three are resolved");
        assert_eq!(held_jobs(&path).len(), 0, "{order:?}");
    }
}

#[tokio::test(start_paused = true)]
async fn resolving_restored_pool_entries_in_any_order_never_runs_more_jobs_than_workers_are_left() {
    for (i, order) in ORDERS.into_iter().enumerate() {
        let path = journal_path(&format!("rebalance-pool-{i}"));
        journal_with(&path, &[(1, Some(0)), (2, Some(1)), (3, None)]);
        let (pool, _log, _t0) = pool_journaled(&path, limits(2, 2), vec![Some(Script::new().req(Req::Silent)), Some(Script::new().req(Req::Silent))]);
        assert_eq!(matches!(pool.enqueue("view", n(1), None).err(), Some(Refusal::Held { .. })), true, "{order:?}");
        let ids = [1u64, 2, 3];
        for k in 0..2 {
            assert_eq!(pool.resolve(ids[order[k]], Resolution::ConfirmedGone, note()), Ok(()), "{order:?}");
        }
        let _tickets: Vec<_> = (0..2).filter_map(|j| pool.enqueue("view", n(j), Some(s(1000))).ok()).collect();
        sleep(s(1)).await;
        let running = pool.status()["running"].as_array().map(|r| r.len()).unwrap_or(0);
        assert_eq!(running <= 1, true, "{order:?}: one child is still unresolved, so at most one of two workers may run: {}", pool.status());
        assert_eq!(held_jobs(&path).len(), 1, "{order:?}: the unresolved one is still on file");
    }
}

// ---- 3. an expired request is never started, and one absolute deadline goes through every layer ----

#[tokio::test]
async fn a_direct_command_whose_budget_has_expired_is_not_started() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let permit = adm.admit("page", &Budget::new(Duration::from_secs(5))).await.ok().expect("a place");
    let r = adm.run_command(permit, Command::new("athena-no-such-program-xyz"), "page", &Budget::new(Duration::ZERO)).await;
    assert_eq!(r.map(|_| ()), Err(DirectError::Timeout), "expired before it began: not even started (an attempt to start it would have said Spawn)");
    assert_eq!(adm.admit("page", &Budget::new(Duration::from_secs(1))).await.is_ok(), true, "the place was never used");
    assert_eq!(adm.status()["kinds"]["page"]["expired"], 1, "and it is counted: {}", adm.status());
}

#[tokio::test(start_paused = true)]
async fn a_job_given_an_absolute_deadline_that_has_already_passed_is_never_given_a_child() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![]);
    let at = Instant::now();
    sleep(s(1)).await;
    let t = pool.enqueue_until("page", n(1), at).expect("accepted, and expired at once");
    assert_eq!(matches!(bounded(t.result()).await, Outcome::Expired(_)), true);
    assert_eq!(events(&log).iter().any(|e| e.starts_with("spawn")), false, "no child was started for it: {:?}", events(&log));
}

#[tokio::test(start_paused = true)]
async fn a_job_given_an_absolute_deadline_is_cut_at_that_instant() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent))]);
    sleep(s(3)).await;
    let t = pool.enqueue_until("page", n(1), t0 + s(10)).unwrap();
    assert_eq!(matches!(bounded(t.result()).await, Outcome::Expired("deadline_exceeded")), true);
    assert_eq!(t0.elapsed(), s(10), "the deadline was the one the request began with, not 10 s from the submission");
}

// ---- 4. ordinary teardown: dropping a pool does not lose a child it could not end ----

#[tokio::test(start_paused = true)]
async fn dropping_a_pool_whose_idle_child_cannot_be_confirmed_gone_keeps_its_quarantine_on_file() {
    let path = journal_path("drop-idle");
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new().immortal())]);
    let a = pool.enqueue("view", n(1), None).unwrap();
    assert_eq!(matches!(bounded(a.result()).await, Outcome::Reply(_)), true, "the job was answered and its child kept warm");
    drop(pool); // ordinary teardown: nobody calls shutdown
    sleep(s(30)).await; // the worker ends its child: terminate, wait, kill, wait
    let j = journal_json(&path);
    assert_eq!((j["held"].as_array().map(|h| h.len()), j["held"][0]["kind"].as_str(), j["held"][0]["pid"].as_u64()), (Some(1), Some("idle_child"), Some(1001)), "the child that could not be ended is on file: {j}");
    let (again, _l, _t) = pool_journaled(&path, limits(1, 1), vec![]);
    assert_eq!(matches!(again.enqueue("view", n(2), None).err(), Some(Refusal::Held { .. })), true, "the restart is quarantined");
}

#[tokio::test(start_paused = true)]
async fn dropping_a_pool_ends_a_warm_child_that_can_be_ended_and_leaves_nothing_on_file() {
    let path = journal_path("drop-killable");
    let (pool, log, _t0) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new())]);
    let a = pool.enqueue("view", n(1), None).unwrap();
    let _ = bounded(a.result()).await;
    drop(pool);
    sleep(s(30)).await;
    assert_eq!(events(&log).iter().any(|e| e.starts_with("exit1")), true, "the child was ended and confirmed gone: {:?}", events(&log));
    assert_eq!(held_jobs(&path).len(), 0, "so there is nothing to quarantine");
}

// ---- 5. a held child that is not on file blocks admission; the journal can be written again without admitting anything ----

#[tokio::test(start_paused = true)]
async fn a_held_child_that_is_not_on_file_blocks_admission_until_the_journal_is_written_again() {
    let path = journal_path("undurable-pool");
    let (pool, log, _t0) = pool_journaled(&path, limits(2, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    pool.fail_next_journal_writes(1);
    let a = pool.enqueue("view", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await; // held at 40; its write failed
    assert_eq!(pool.status()["journal"]["error"].is_string(), true, "{}", pool.status());
    assert_eq!(pool.enqueue("view", n(2), None).err(), Some(Refusal::Unrecovered), "a worker is idle, but a held child is not on file: nothing is admitted");
    assert_eq!(pool.status()["journal"]["blocking"], true, "the status says admission is blocked by it: {}", pool.status());
    pool.fail_next_journal_writes(1);
    assert_eq!(pool.resolve(0, Resolution::RetryJournal, note()), Err(ResolveError::JournalError), "a retry that fails changes nothing");
    assert_eq!(pool.enqueue("view", n(2), None).err(), Some(Refusal::Unrecovered));
    assert_eq!(pool.resolve(0, Resolution::RetryJournal, OverrideNote::new("asha", "disk was full, freed it").unwrap()), Ok(()));
    assert_eq!(held_jobs(&path), vec![aid], "the held child is on file now");
    assert_eq!(pool.status()["journal"]["error"].is_null(), true, "{}", pool.status());
    assert_eq!(pool.status()["recovery"]["acknowledgements"][0]["action"], "retry_journal", "who did it is on record: {}", pool.status());
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 1, "the retry admitted and started nothing");
    let b = pool.enqueue("view", n(2), None).expect("admitted once the journal is written");
    assert_eq!(matches!(bounded(b.result()).await, Outcome::Reply(_)), true);
}

#[tokio::test(start_paused = true)]
async fn the_hold_task_writes_the_journal_again_by_itself_without_admitting_anything() {
    let path = journal_path("undurable-auto");
    let (pool, log, _t0) = pool_journaled(&path, limits(2, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    pool.fail_next_journal_writes(1);
    let a = pool.enqueue("view", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await; // held at 40, write failed
    assert_eq!(held_jobs(&path).len(), 0);
    sleep(s(16)).await; // the next recheck, at 55
    assert_eq!(held_jobs(&path), vec![aid], "the retry made on the recheck wrote it");
    assert_eq!(pool.status()["journal"]["error"].is_null(), true, "{}", pool.status());
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 1, "and admitted nothing");
}

#[tokio::test(start_paused = true)]
async fn a_failed_write_with_nothing_held_does_not_block_admission() {
    let path = journal_path("undurable-empty");
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue("view", n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    pool.fail_next_journal_writes(1);
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone, note()), Ok(()), "freed, and the write that says so fails");
    sleep(s(1)).await;
    assert_eq!(pool.status()["journal"]["error"].is_string(), true, "{}", pool.status());
    assert_eq!(pool.enqueue("view", n(2), None).is_ok(), true, "nothing is held, so the stale file is only over-cautious, not unsafe: work is admitted");
}

#[tokio::test(start_paused = true)]
async fn a_retry_of_the_journal_when_nothing_failed_is_refused() {
    let path = journal_path("undurable-none");
    let (pool, _log, _t0) = pool_journaled(&path, limits(1, 1), vec![]);
    assert_eq!(pool.resolve(0, Resolution::RetryJournal, note()), Err(ResolveError::NotBlocked));
    let adm = Arc::new(Admission::with_journal("t", 1, 0, journal_path("undurable-none-adm")));
    assert_eq!(adm.resolve(0, Resolution::RetryJournal, note()), Err(ResolveError::NotBlocked));
}

#[tokio::test(start_paused = true)]
async fn a_held_direct_command_that_is_not_on_file_blocks_the_admission_until_the_journal_is_written_again() {
    let path = journal_path("undurable-adm");
    let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    adm.fail_next_journal_writes(1);
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(11)).await; // (the call returned at 10) held at 20, write failed; 21 now, before the recheck at 35
    assert_eq!(adm.status()["journal"]["error"].is_string(), true, "{}", adm.status());
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.map(|_| ()), Err(AdmitError::Held), "one of two places is free, but the held command is not on file");
    assert_eq!(adm.resolve(0, Resolution::RetryJournal, OverrideNote::new("asha", "freed the disk").unwrap()), Ok(()));
    assert_eq!(held_jobs(&path).len(), 1, "it is on file now");
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true, "and the free place can be used again");
}

#[tokio::test(start_paused = true)]
async fn a_request_waiting_for_a_place_is_told_the_admission_is_closed_when_it_shuts_down() {
    let adm = Arc::new(Admission::new("t", 1, 2));
    let held = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let waiter = tokio::spawn({ let adm = adm.clone(); async move { adm.admit("page", &Budget::new(s(1000))).await.map(|_| ()) } });
    sleep(s(1)).await; // it is waiting
    assert_eq!(bounded(PoolView::shutdown(&*adm)).await, ShutdownReport::default());
    assert_eq!(bounded(waiter).await.unwrap(), Err(AdmitError::Closed), "it is told at once, not left to wait out its deadline");
    drop(held);
}

#[tokio::test(start_paused = true)]
async fn a_command_is_not_started_once_shutdown_began() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    assert_eq!(bounded(PoolView::shutdown(&*adm)).await, ShutdownReport::default());
    let r = adm.run_command(permit, Command::new("athena-no-such-program-xyz"), "page", &Budget::new(s(100))).await;
    assert_eq!(r.map(|_| ()), Err(DirectError::Shutdown), "not started (an attempt to start it would have said Spawn)");
}

#[tokio::test(start_paused = true)]
async fn a_failed_write_with_nothing_held_does_not_block_the_admission_either() {
    let path = journal_path("undurable-adm-empty");
    let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(11)).await; // (the call returned at 10) held at 20, on file
    let id = held_of(&adm)[0]["id"].as_u64().unwrap();
    adm.fail_next_journal_writes(1);
    assert_eq!(adm.resolve(id, Resolution::ConfirmedGone, note()), Ok(()), "freed, and the write that says so fails");
    sleep(s(1)).await;
    assert_eq!(adm.status()["journal"]["error"].is_string(), true, "{}", adm.status());
    assert_eq!(adm.status()["journal"]["blocking"], false, "the error is shown, but nothing is held, so nothing is blocked: {}", adm.status());
    assert_eq!(bounded(adm.admit("page", &Budget::new(s(1)))).await.is_ok(), true, "nothing is held: the stale file is over-cautious, not unsafe");
}

#[tokio::test(start_paused = true)]
async fn the_direct_owner_writes_the_journal_again_by_itself_at_every_recheck() {
    let path = journal_path("undurable-adm-auto");
    let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    adm.fail_next_journal_writes(1);
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(11)).await; // (the call returned at 10) held at 20, write failed
    assert_eq!(held_jobs(&path).len(), 0);
    sleep(s(15)).await; // 36: the recheck at 35 wrote it
    assert_eq!(held_jobs(&path).len(), 1, "written again by the owner");
    assert_eq!(adm.status()["journal"]["error"].is_null(), true, "{}", adm.status());
}
// ======================================================================= T6 review 4: concurrent shutdown, counting, the status payload, launch against shutdown

fn hold_launches(adm: &Admission) -> (Arc<Notify>, Arc<Notify>) {
    let (reached, go) = (Arc::new(Notify::new()), Arc::new(Notify::new()));
    let (r, g) = (reached.clone(), go.clone());
    adm.on_launch_gap(Arc::new(move || {
        let (r, g) = (r.clone(), g.clone());
        Box::pin(async move {
            r.notify_one();
            g.notified().await;
        })
    }));
    (reached, go)
}

// ---- 1. every caller of a concurrent shutdown sees it complete ----

#[tokio::test(start_paused = true)]
async fn every_caller_of_a_concurrent_admission_shutdown_returns_when_the_last_owner_is_gone() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _caller = tokio::spawn({ let (adm, log) = (adm.clone(), log.clone()); async move { adm.run_direct(permit, FakeDirect::new(DScript::overruns_and_exits_after_a_kill(s(1)), &log, t0, 1), "page", &Budget::new(s(100))).await } });
    sleep(s(2)).await;
    let (a, b, c) = (tokio::spawn({ let adm = adm.clone(); async move { PoolView::shutdown(&*adm).await } }), tokio::spawn({ let adm = adm.clone(); async move { PoolView::shutdown(&*adm).await } }), tokio::spawn({ let adm = adm.clone(); async move { PoolView::shutdown(&*adm).await } }));
    let (ra, rb, rc) = bounded(async { tokio::join!(a, b, c) }).await;
    let one = ShutdownReport { ended: 1, unconfirmed: 0 };
    assert_eq!((ra.unwrap(), rb.unwrap(), rc.unwrap()), (one, one, one), "all three callers were told it completed, with the same report");
}

#[tokio::test(start_paused = true)]
async fn every_caller_of_a_concurrent_pool_shutdown_returns_when_the_last_worker_has_stopped() {
    let (pool, _log, _t0) = pool_of(limits(2, 2), vec![Some(Script::new().req(Req::Silent)), Some(Script::new().req(Req::Silent))]);
    let a = pool.enqueue("view", n(1), None).unwrap();
    let b = pool.enqueue("view", n(2), None).unwrap();
    sleep(s(2)).await;
    let (x, y, z) = bounded(async { tokio::join!(pool.shutdown(), pool.shutdown(), pool.shutdown()) }).await;
    assert_eq!((x, y), (y, z), "every caller got the report");
    assert_eq!(x.unconfirmed, 0);
    drop((a, b));
}

// ---- 2. every owner is counted once, whichever way its child came to be ended ----

#[tokio::test(start_paused = true)]
async fn a_child_being_ended_after_its_deadline_when_shutdown_begins_is_counted_as_ended() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let r = bounded(adm.run_direct(permit, FakeDirect::new(DScript::overruns_and_exits_after_a_kill(s(3)), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    assert_eq!(r, Err(DirectError::Timeout));
    sleep(s(1)).await; // 11: killed at 10, gone at 13
    let report = bounded(PoolView::shutdown(&*adm)).await;
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 }, "the child exited while shutdown waited for it: it was ended");
    assert_eq!(events(&log), vec!["kill1@10", "exit1@13"]);
}

#[tokio::test(start_paused = true)]
async fn a_child_that_stays_unconfirmed_through_its_cleanup_and_the_last_attempt_is_counted_once() {
    let path = journal_path("count-once");
    let adm = Arc::new(Admission::with_journal("t", 1, 0, path.clone()));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(1)).await; // 11: being ended (until 20)
    let report = bounded(PoolView::shutdown(&*adm)).await;
    assert_eq!(report, ShutdownReport { ended: 0, unconfirmed: 1 }, "one owner, one count: not one for the cleanup and another for the last attempt");
    assert_eq!(held_jobs(&path).len(), 1, "and it is on file");
}

#[tokio::test(start_paused = true)]
async fn a_held_child_confirmed_by_a_periodic_attempt_that_shutdown_overlaps_is_counted_as_ended() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::exits_after_kills(3), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(25) + Duration::from_millis(500)).await; // 35.5: the recheck at 35 is making the third attempt, which is confirmed at 36
    let report = bounded(PoolView::shutdown(&*adm)).await;
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 }, "{:?}", events(&log));
}

// ---- 3. the registry's payload for a direct admission carries journal.blocking ----

#[tokio::test(start_paused = true)]
async fn the_registry_payload_of_a_direct_admission_says_whether_the_journal_blocks_it_before_while_and_after() {
    let path = journal_path("registry-blocking");
    let adm = Arc::new(Admission::with_journal("t", 2, 0, path.clone()));
    let registry = WorkRegistry::new();
    registry.register(adm.view());
    let blocking = || registry.status()["pools"][0]["journal"]["blocking"].clone();
    assert_eq!(blocking(), json!(false), "before any failure: {}", registry.status());
    adm.fail_next_journal_writes(1);
    let (log, t0): (Log, Instant) = (Arc::new(Mutex::new(Vec::new())), Instant::now());
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let _ = bounded(adm.run_direct(permit, FakeDirect::new(DScript::immortal(), &log, t0, 1), "page", &Budget::new(s(10)))).await;
    sleep(s(11)).await; // held at 20, its write failed
    assert_eq!(blocking(), json!(true), "while blocked: {}", registry.status());
    assert_eq!(registry.status()["pools"][0]["journal"]["error"].is_string(), true);
    assert_eq!(adm.resolve(0, Resolution::RetryJournal, note()), Ok(()));
    assert_eq!(blocking(), json!(false), "after the retry worked: {}", registry.status());
}

// ---- 4. a launch racing with shutdown is refused or waited for ----

#[tokio::test(start_paused = true)]
async fn a_launch_that_passed_its_check_when_shutdown_begins_is_waited_for_by_shutdown() {
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (reached, go) = hold_launches(&adm);
    let permit = bounded(adm.admit("page", &Budget::new(s(100)))).await.ok().expect("a place");
    let launch = tokio::spawn({ let adm = adm.clone(); async move { adm.run_command(permit, Command::new("athena-no-such-program-xyz"), "page", &Budget::new(s(100))).await.map(|_| ()) } });
    bounded(reached.notified()).await; // the launch has decided it may start, and has not started
    let shutting = tokio::spawn({ let adm = adm.clone(); async move { PoolView::shutdown(&*adm).await } });
    sleep(s(5)).await;
    assert_eq!(shutting.is_finished(), false, "shutdown returned while a launch was between its decision and its start");
    go.notify_one();
    let r = bounded(launch).await.unwrap();
    assert_eq!(matches!(r, Err(DirectError::Spawn) | Err(DirectError::Shutdown)), true, "{r:?}");
    assert_eq!(bounded(shutting).await.unwrap(), ShutdownReport::default());
}

#[tokio::test]
async fn a_real_launch_held_at_the_gap_when_shutdown_begins_is_ended_and_counted_by_that_shutdown() {
    let Some(py) = py() else { return };
    let adm = Arc::new(Admission::new("t", 1, 0));
    let (reached, go) = hold_launches(&adm);
    let permit = adm.admit("page", &Budget::new(Duration::from_secs(5))).await.ok().expect("a place");
    let launch = tokio::spawn({ let adm = adm.clone(); async move { let mut cmd = Command::new(py); cmd.args(["-c", "import time\ntime.sleep(60)"]); adm.run_command(permit, cmd, "page", &Budget::new(Duration::from_secs(30))).await.map(|_| ()) } });
    tokio::time::timeout(Duration::from_secs(5), reached.notified()).await.expect("the launch reached the gap");
    let shutting = tokio::spawn({ let adm = adm.clone(); async move { PoolView::shutdown(&*adm).await } });
    sleep(Duration::from_millis(300)).await;
    assert_eq!(shutting.is_finished(), false, "shutdown must wait for the launch");
    go.notify_one();
    let r = tokio::time::timeout(Duration::from_secs(10), launch).await.expect("the launch came back").unwrap();
    assert_eq!(r, Err(DirectError::Shutdown), "the process was started and then ended by shutdown");
    let report = tokio::time::timeout(Duration::from_secs(10), shutting).await.expect("shutdown completed").unwrap();
    assert_eq!(report, ShutdownReport { ended: 1, unconfirmed: 0 }, "and the report includes it");
}
