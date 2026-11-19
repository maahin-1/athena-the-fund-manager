use crate::bounded::*;
use std::sync::{Arc,atomic::{AtomicUsize,Ordering}};
use std::time::Duration;
use serde_json::{json,Value};
#[derive(Clone,Default)]
struct Counts { requests:Arc<AtomicUsize>, drops:Arc<AtomicUsize> }
struct Fake(Counts);
impl Drop for Fake { fn drop(&mut self){ self.0.drops.fetch_add(1,Ordering::SeqCst); } }
impl Live for Fake {
 async fn ready(&mut self,_:Duration)->bool{true}
 async fn request(&mut self,_:&Value)->Option<Value>{self.0.requests.fetch_add(1,Ordering::SeqCst);Some(json!({"ok":true}))}
 fn terminate(&mut self){}
 async fn wait_exit(&mut self,_:Duration)->bool{true}
 fn kill(&mut self){}
}
impl Launcher for Counts { type Live=Fake; fn spawn(&self)->Option<Fake>{Some(Fake(self.clone()))} }
fn pool(c:Counts)->Pool<Counts>{ Pool::new("review",c,Limits{deadline:Duration::from_secs(5),..Limits::default()}) }
#[tokio::test]
async fn review_cancelled_before_pickup_must_not_execute(){
 let c=Counts::default();let p=pool(c.clone());let t=p.enqueue(json!({}),None).unwrap();
 assert!(p.cancel(t.id));
 let out=t.result().await;
 assert_eq!(c.requests.load(Ordering::SeqCst),0,"cancelled before pickup still executed; outcome={out:?}");
}
#[tokio::test]
async fn review_expired_before_pickup_must_not_execute(){
 let c=Counts::default();let p=pool(c.clone());let t=p.enqueue(json!({}),Some(Duration::from_millis(5))).unwrap();
 std::thread::sleep(Duration::from_millis(25));
 let out=t.result().await;
 assert_eq!(c.requests.load(Ordering::SeqCst),0,"expired before pickup still executed; outcome={out:?}");
}
#[tokio::test]
async fn review_last_ticket_release_must_drop_idle_child(){
 let c=Counts::default();let p=pool(c.clone());let t=p.enqueue(json!({}),None).unwrap();
 drop(p);
 for _ in 0..8 {tokio::task::yield_now().await;}
 let _=t.result().await;
 for _ in 0..8 {tokio::task::yield_now().await;}
 assert_eq!(c.drops.load(Ordering::SeqCst),1,"idle child/task still waiting after pool and ticket dropped");
}
