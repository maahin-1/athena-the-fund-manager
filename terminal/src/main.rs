mod analyse_worker;
mod analysis;
mod angel;
mod bars;
mod config;
mod fake;
mod fund_fetch;
mod indices;
mod fundamentals;
mod hub;
mod layouts;
mod search;
mod poll;
mod quote;
mod server;
mod totp;
mod verdict_job;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio::sync::watch;
use tokio_util::sync::CancellationToken;

use crate::angel::stream::FeedStatus;
use crate::hub::Hub;
use crate::server::{router, AppState};

struct Args {
    settings_file: PathBuf,
    check_login: bool,
    fake_feed: bool,
    port: u16,
    web_dir: PathBuf,
    warehouse: PathBuf,
    layouts_dir: PathBuf,
    fund_store: PathBuf,
    fund_python: Option<String>,
    python: String,
}

fn args() -> Args {
    let mut out = Args {
        settings_file: PathBuf::from(".env"),
        check_login: false,
        fake_feed: false,
        port: 8700,
        web_dir: PathBuf::from("terminal/web/dist"),
        warehouse: PathBuf::from(".athena/warehouse.duckdb"),
        layouts_dir: PathBuf::from(".athena/terminal/layouts"),
        fund_store: PathBuf::from(".athena/fundamentals.duckdb"),
        fund_python: None,
        python: ".venv/Scripts/python.exe".to_string(),
    };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--env-file" => {
                if let Some(v) = it.next() {
                    out.settings_file = PathBuf::from(v);
                }
            }
            "--check-login" => out.check_login = true,
            "--fake-feed" => out.fake_feed = true,
            "--port" => {
                if let Some(v) = it.next().and_then(|v| v.parse().ok()) {
                    out.port = v;
                }
            }
            "--warehouse" => {
                if let Some(v) = it.next() {
                    out.warehouse = PathBuf::from(v);
                }
            }
            "--python" => {
                if let Some(v) = it.next() {
                    out.python = v;
                }
            }
            "--layouts-dir" => {
                if let Some(v) = it.next() {
                    out.layouts_dir = PathBuf::from(v);
                }
            }
            "--fund-python" => out.fund_python = it.next(),
            "--fund-store" => {
                if let Some(v) = it.next() {
                    out.fund_store = PathBuf::from(v);
                }
            }
            "--web-dir" => {
                if let Some(v) = it.next() {
                    out.web_dir = PathBuf::from(v);
                }
            }
            _ => {}
        }
    }
    out
}

fn now_unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// Log in, load the instrument list and run the stream; if the feed stays down for ten minutes (an expired login, for instance) start over.
/// Nothing here can stop the server: every failure is a short status phrase and a retry.
async fn supervise(settings_file: PathBuf, hub: Hub, status: watch::Sender<FeedStatus>, shutdown: CancellationToken) {
    let client = reqwest::Client::new();
    let retry = Duration::from_secs(60);
    while !shutdown.is_cancelled() {
        let _ = status.send(FeedStatus::Connecting);
        let attempt: Result<(angel::login::Session, Arc<angel::master::Master>), &'static str> = async {
            let settings = config::read(&settings_file).map_err(|_| "settings missing")?;
            let session = angel::login::login(&client, &settings, now_unix()).await.map_err(|_| "login failed")?;
            let master = angel::master::load(&client, std::path::Path::new(".athena/angel_master.json"))
                .await
                .map_err(|_| "instrument list unavailable")?;
            Ok((session, Arc::new(master)))
        }
        .await;
        match attempt {
            Err(reason) => {
                let _ = status.send(FeedStatus::Off(reason));
                tokio::select! { _ = shutdown.cancelled() => return, _ = tokio::time::sleep(retry) => {} }
            }
            Ok((session, master)) => {
                let child = shutdown.child_token();
                let stream = tokio::spawn(angel::stream::run_stream(session, master, hub.clone(), status.clone(), child.clone()));
                let mut off_since: Option<Instant> = None;
                loop {
                    tokio::select! { _ = shutdown.cancelled() => break, _ = tokio::time::sleep(Duration::from_secs(30)) => {} }
                    let down = matches!(*status.borrow(), FeedStatus::Off(_));
                    off_since = if down { off_since.or(Some(Instant::now())) } else { None };
                    if off_since.is_some_and(|t| t.elapsed() > Duration::from_secs(600)) {
                        break;
                    }
                }
                child.cancel();
                let _ = stream.await;
            }
        }
    }
}

#[tokio::main]
async fn main() {
    let args = args();
    if args.check_login {
        let settings = match config::read(&args.settings_file) {
            Ok(s) => s,
            Err(e) => {
                println!("error: {e}");
                std::process::exit(1);
            }
        };
        match angel::login::login(&reqwest::Client::new(), &settings, now_unix()).await {
            Ok(session) => println!(
                "login works for {}; feed token {}",
                session.client_code,
                if session.feed_token.is_empty() { "NOT received" } else { "received" }
            ),
            Err(e) => {
                println!("error: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

    let listener = match tokio::net::TcpListener::bind(("127.0.0.1", args.port)).await {
        Ok(l) => l,
        Err(_) => {
            println!("error: port {} is in use", args.port);
            std::process::exit(1);
        }
    };
    let port = listener.local_addr().map(|a| a.port()).unwrap_or(args.port);
    let hub = Hub::new();
    let (status, _keep) = watch::channel(if args.fake_feed { FeedStatus::On } else { FeedStatus::Connecting });
    let shutdown = CancellationToken::new();
    let index_store = indices::IndexStore::new();
    if args.fake_feed {
        tokio::spawn(fake::run_fake(hub.clone(), shutdown.clone()));
        tokio::spawn(indices::run_fake(index_store.clone(), shutdown.clone()));
    } else {
        tokio::spawn(indices::run_refresher(index_store.clone(), reqwest::Client::new(), shutdown.clone()));
        tokio::spawn(supervise(args.settings_file.clone(), hub.clone(), status.clone(), shutdown.clone()));
        tokio::spawn(poll::run_poller(hub.clone(), reqwest::Client::new(), shutdown.clone()));
    }
    let bars = Arc::new(bars::Bars {
        adjusted: bars::AdjustedDaily::python(args.python.clone(), args.warehouse.clone()),
        warehouse: args.warehouse,
        intraday: bars::Intraday::new(bars::yahoo_fetcher(reqwest::Client::new())),
    });
    let lists = Arc::new(layouts::LayoutStore::new(args.layouts_dir.parent().map(|p| p.join("lists")).unwrap_or_else(|| PathBuf::from(".athena/terminal/lists"))));
    let layouts = Arc::new(layouts::LayoutStore::new(args.layouts_dir));
    let verdicts = verdict_job::VerdictJobs::standard(&args.python);
    let analyse = Arc::new(analyse_worker::AnalyseWorker::standard(&args.python));
    let fundamentals = Arc::new(fundamentals::FundamentalsJobs::standard(args.fund_python.as_deref().unwrap_or(&args.python), bars.warehouse.clone(), args.fund_store.clone()));
    let fund_fetch = fund_fetch::FundFetch::standard(args.fund_python.as_deref().unwrap_or(&args.python), args.fund_store.clone());
    let search = Arc::new(search::SearchCache::new(bars.warehouse.clone()));
    let state = AppState { hub, status, port, web_dir: args.web_dir, bars, layouts, verdicts, analyse, fundamentals, indices: index_store, lists, search, fund_fetch };
    println!("athena-terminal on http://127.0.0.1:{port}{}", if args.fake_feed { " (fake feed)" } else { "" });
    let stop = shutdown.clone();
    let result = axum::serve(listener, router(state))
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            stop.cancel();
        })
        .await;
    if result.is_err() {
        println!("error: the server stopped");
    }
}
