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
    c.enqueue(s(now), json!({}), s(deadline), None).expect("accepted")
}

#[test]
fn a_submission_past_capacity_is_refused_at_once_and_the_running_and_queued_jobs_are_untouched() {
    let mut c = core(1, 2);
    let (a, b, d) = (put(&mut c, 0, 300), put(&mut c, 0, 300), put(&mut c, 0, 300));
    assert_eq!(c.slot_view(0), SlotView::Busy(a));
    assert_eq!(c.queued_ids(), vec![b, d]);
    assert_eq!(c.enqueue(s(1), json!({}), s(300), None), Err(Refusal::QueueFull { capacity: 2 }));
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
    assert_eq!(c.enqueue(s(0), json!({}), s(300), None), Err(Refusal::QueueFull { capacity: 0 }));
    let mut two = core(2, 1);
    let ids: Vec<u64> = (0..3).map(|_| put(&mut two, 0, 300)).collect();
    assert_eq!((two.slot_view(0), two.slot_view(1), two.queued_ids()), (SlotView::Busy(ids[0]), SlotView::Busy(ids[1]), vec![ids[2]]));
    assert!(two.enqueue(s(0), json!({}), s(300), None).is_err());
}

#[test]
fn a_job_whose_deadline_passes_while_queued_expires_never_starts_and_frees_its_place() {
    let mut c = core(1, 1);
    let a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 10);
    assert!(c.enqueue(s(1), json!({}), s(300), None).is_err(), "the place is taken");
    assert_eq!(c.sweep(Duration::from_millis(9_999)), Vec::<u64>::new(), "one millisecond before its deadline it is still waiting");
    assert_eq!(c.sweep(s(10)), vec![b], "at its deadline it has expired");
    let r = c.record(b).unwrap();
    assert_eq!((r.state, r.reason.as_deref(), r.queue_wait_ms, r.run_ms), (JobState::Expired, Some("deadline_before_start"), Some(10_000), None));
    assert_eq!(c.slot_view(0), SlotView::Busy(a), "the running job is untouched, and b was never given the slot");
    assert!(c.queued_ids().is_empty());
    let next = c.enqueue(s(11), json!({}), s(300), None).expect("the freed place is usable");
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
    assert!(matches!(c.cancel(s(5), a), Cancelled::Running(_)));
    assert_eq!(c.record(a).unwrap().state, JobState::Running);
    assert_eq!(c.slot_view(0), SlotView::Busy(a), "the slot is held until the worker says the child is gone");
    assert_eq!(c.queued_ids(), vec![b]);
    // the same once the worker has picked the job up (the slot is then `Busy`, not merely assigned)
    c.take_assigned(0).expect("picked up");
    assert_eq!(matches!(c.cancel(s(6), a), Cancelled::Running(_)), true, "a job a worker is running is signalled too");
    assert_eq!(c.slot_view(0), SlotView::Busy(a));
}

#[test]
fn a_place_held_by_a_job_that_has_expired_is_free_at_the_next_submission_without_anyone_sweeping() {
    let mut c = core(1, 1);
    let _a = put(&mut c, 0, 300);
    let b = put(&mut c, 0, 10); // takes the only place
    assert!(c.enqueue(s(5), json!({}), s(300), None).is_err(), "full while b waits");
    let next = c.enqueue(s(11), json!({}), s(300), None);
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
    assert_eq!(c.enqueue(s(31), json!({}), s(300), None), Err(Refusal::Held { job: a }), "a worker that cannot be freed takes no new job");
    assert_eq!(c.status(s(31))["totals"]["refused_held"], 1);
    assert_eq!(c.status(s(31))["retained"][0]["job"], a);
    assert_eq!(c.request_resolution(a, Resolution::ConfirmedGone), Ok(0));
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
    assert_eq!(c.request_resolution(a, Resolution::ConfirmedGone), Err(ResolveError::NotRetained), "a running job is not retained");
    assert_eq!(c.request_resolution(999, Resolution::Retry), Err(ResolveError::NotRetained));
    c.finish(s(5), 0, a, Finish { state: JobState::Cancelled, reason: "cancelled_while_running", retain: true });
    assert_eq!(c.slot_view(1), SlotView::Busy(b));
    let later = c.enqueue(s(6), json!({}), s(300), None);
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
}
impl Script {
    fn new() -> Script {
        Script { ready_never: false, ready_after: Duration::ZERO, reqs: vec![], on_terminate: Exit::After(s(1)), on_kill: Exit::After(Duration::ZERO) }
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
        let ok = tokio::time::timeout(limit, sleep(self.script.ready_after)).await.is_ok();
        if ok {
            self.note("ready");
        }
        ok
    }
    async fn request(&mut self, payload: &Value) -> Option<Value> {
        self.note("request");
        match self.reqs.pop_front().unwrap_or(Req::Answer(Duration::ZERO)) {
            Req::Answer(d) => {
                sleep(d).await;
                Some(json!({"ok": true, "result": payload["n"].clone()}))
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
    let a = pool.enqueue(n(1), None).unwrap();
    let b = pool.enqueue(n(2), None).unwrap();
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
    let a = pool.enqueue(n(1), None).unwrap();
    let b = pool.enqueue(n(2), Some(s(50))).unwrap();
    assert!(matches!(pool.enqueue(n(3), None), Err(Refusal::QueueFull { capacity: 1 })));
    let hb = tokio::spawn(b.result());
    let rb = bounded(hb).await.unwrap();
    assert_eq!(rb, Outcome::Expired("deadline_before_start"));
    assert_eq!(t0.elapsed(), s(50), "at its own deadline, while the first job still runs");
    let c = pool.enqueue(n(4), None).expect("its place is free again");
    let (ra, rc) = bounded(async { tokio::join!(a.result(), c.result()) }).await;
    assert!(matches!(ra, Outcome::Reply(_)) && matches!(rc, Outcome::Reply(_)), "the others are unaffected");
    let requests = events(&log).iter().filter(|e| e.starts_with("request")).count();
    assert_eq!(requests, 2, "b's payload was never sent to a child");
    assert_eq!(record(&pool, 2)["state"], "expired");
}

#[tokio::test(start_paused = true)]
async fn a_submission_past_capacity_is_refused_synchronously_and_the_jobs_already_accepted_finish_normally() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Answer(s(10))).req(Req::Answer(s(10))))]);
    let a = pool.enqueue(n(1), None).unwrap();
    let b = pool.enqueue(n(2), None).unwrap();
    for _ in 0..3 {
        assert!(matches!(pool.enqueue(n(9), None), Err(Refusal::QueueFull { capacity: 1 })), "refused without waiting");
    }
    assert_eq!(t0.elapsed(), Duration::ZERO, "no time passed: nothing waited for a place");
    let (ra, rb) = bounded(async { tokio::join!(a.result(), b.result()) }).await;
    assert!(matches!((ra, rb), (Outcome::Reply(_), Outcome::Reply(_))));
    assert_eq!(pool.status()["totals"]["refused_queue_full"], 3);
}

#[tokio::test(start_paused = true)]
async fn dropping_a_queued_ticket_cancels_it_without_touching_the_running_job() {
    let (pool, log, _t0) = pool_of(limits(1, 3), vec![Some(Script::new().req(Req::Answer(s(100))))]);
    let a = pool.enqueue(n(1), None).unwrap();
    let b = pool.enqueue(n(2), None).unwrap();
    let c = pool.enqueue(n(3), None).unwrap();
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
    let a = pool.enqueue(n(1), None).unwrap();
    let b = pool.enqueue(n(2), None).unwrap();
    let aid = a.id;
    let hb = tokio::spawn(b.result());
    sleep(s(10)).await;
    drop(a); // the caller went away
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
    let a = pool.enqueue(n(1), Some(s(30))).unwrap();
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
    let a = pool.enqueue(n(1), Some(s(30))).unwrap();
    let aid = a.id;
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_exceeded"), "the caller is told at its deadline, not after the child is dealt with");
    assert_eq!(t0.elapsed(), s(30));
    sleep(s(11)).await; // terminate at 30, kill at 35, the second wait ends at 40
    assert_eq!(record(&pool, aid)["state"], "cancel_unconfirmed");
    let st = pool.status();
    assert_eq!(st["retained"][0]["job"], aid, "{st}");
    assert!(matches!(pool.enqueue(n(2), None), Err(Refusal::Held { job }) if job == aid), "no new job takes a worker that cannot be freed");
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 1, "no second child was started");
    assert_eq!(pool.resolve(aid, Resolution::ConfirmedGone), Ok(()));
    sleep(s(1)).await;
    assert_eq!(pool.status()["retained"].as_array().unwrap().len(), 0, "released by the person");
    let r = record(&pool, aid);
    assert_eq!((r["state"].as_str(), r["resolution"].as_str()), (Some("expired"), Some("resolved_by_person")));
    let b = pool.enqueue(n(2), None).expect("the worker is free again");
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)));
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 2, "a fresh child, since the old one was given up");
}

#[tokio::test(start_paused = true)]
async fn the_pool_rechecks_a_retained_child_and_releases_the_guard_when_it_is_finally_gone() {
    // ignores terminate, exits 60 s after the kill: not confirmed at 40, confirmed by a later recheck
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).terminate(Exit::Never).kill(Exit::After(s(60))))]);
    let a = pool.enqueue(n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    assert_eq!(record(&pool, aid)["state"], "cancel_unconfirmed");
    assert!(matches!(pool.enqueue(n(2), None), Err(Refusal::Held { .. })));
    sleep(s(80)).await;
    assert_eq!(pool.status()["retained"].as_array().unwrap().len(), 0, "released once the exit was confirmed");
    let r = record(&pool, aid);
    assert_eq!((r["state"].as_str(), r["resolution"].as_str()), (Some("expired"), Some("confirmed_exit_later")));
    assert!(pool.enqueue(n(2), None).is_ok());
}

#[tokio::test(start_paused = true)]
async fn a_person_can_ask_for_another_attempt_and_only_a_retained_job_can_be_resolved() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Silent).immortal())]);
    let a = pool.enqueue(n(1), Some(s(30))).unwrap();
    let aid = a.id;
    let _ = bounded(a.result()).await;
    sleep(s(11)).await;
    assert_eq!(pool.resolve(aid + 7, Resolution::ConfirmedGone), Err(ResolveError::NotRetained));
    let before = events(&log).iter().filter(|e| e.starts_with("terminate")).count();
    assert_eq!(pool.resolve(aid, Resolution::Retry), Ok(()));
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
    let a = pool.enqueue(n(1), Some(s(60))).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_during_start"));
    assert_eq!(t0.elapsed(), s(60));
    sleep(s(5)).await;
    assert!(events(&log).contains(&"terminate1@60".to_string()), "a child that is still starting is ended by the same routine: {:?}", events(&log));
    assert!(events(&log).iter().all(|e| !e.starts_with("request")), "nothing was asked of a child that never became ready");
}

#[tokio::test(start_paused = true)]
async fn a_child_that_ignores_its_start_limit_cannot_outlast_the_jobs_deadline() {
    let (pool, log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().never_ready())]);
    let a = pool.enqueue(n(1), Some(s(90))).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Expired("deadline_during_start"), "the start limit (60 s) was ignored by the child, so the deadline (90 s) is what ends it, and it is named as such");
    assert_eq!(t0.elapsed(), s(90));
    sleep(s(2)).await;
    assert!(events(&log).contains(&"terminate1@90".to_string()), "{:?}", events(&log));
}

#[tokio::test(start_paused = true)]
async fn startup_has_its_own_limit_inside_the_deadline() {
    let (pool, _log, t0) = pool_of(limits(1, 1), vec![Some(Script::new().ready_after(s(100)))]);
    let a = pool.enqueue(n(1), Some(s(300))).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("start_failed"));
    assert_eq!(t0.elapsed(), s(60), "the start limit (60 s) applies though the deadline is 300 s");
}

#[tokio::test(start_paused = true)]
async fn a_start_that_cannot_spawn_fails_at_once_and_does_not_hold_the_worker() {
    let (pool, _log, _t0) = pool_of(limits(1, 1), vec![None, Some(Script::new())]);
    let a = pool.enqueue(n(1), None).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("start_failed"));
    let b = pool.enqueue(n(2), None).unwrap();
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)), "the next job starts a fresh child");
}

#[tokio::test(start_paused = true)]
async fn a_child_that_dies_mid_job_fails_the_job_and_is_confirmed_gone_before_the_next_starts() {
    let (pool, log, _t0) = pool_of(limits(1, 1), vec![Some(Script::new().req(Req::Closes(s(5))))]);
    let a = pool.enqueue(n(1), None).unwrap();
    assert_eq!(bounded(a.result()).await, Outcome::Failed("no_answer"));
    let b = pool.enqueue(n(2), None).unwrap();
    assert!(matches!(bounded(b.result()).await, Outcome::Reply(_)));
    let ev = events(&log);
    assert!(ev.iter().position(|e| e.starts_with("exit1")).unwrap() < ev.iter().position(|e| e.starts_with("spawn2")).unwrap(), "{ev:?}");
}

#[tokio::test(start_paused = true)]
async fn a_healthy_child_is_kept_between_jobs_and_a_failure_reply_does_not_end_it() {
    let (pool, log, _t0) = pool_of(limits(1, 2), vec![Some(Script::new().req(Req::Fail(s(1))).req(Req::Answer(s(1))))]);
    let a = pool.enqueue(n(1), None).unwrap();
    let aid = a.id;
    let ra = bounded(a.result()).await;
    assert_eq!(ra, Outcome::Reply(json!({"ok": false, "error": "no such instrument"})));
    let b = pool.enqueue(n(2), None).unwrap();
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
    let a = pool.enqueue(n(1), None).unwrap();
    let b = pool.enqueue(n(2), None).unwrap();
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
    let a = pool.enqueue(n(1), None).unwrap();
    assert!(matches!(bounded(a.result()).await, Outcome::Reply(_)));
    assert_eq!(events(&log).iter().filter(|e| e.starts_with("spawn")).count(), 1, "the warm child served the request");
}
