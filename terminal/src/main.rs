mod angel;
mod config;
mod hub;
mod poll;
mod quote;
mod totp;

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

struct Args {
    settings_file: PathBuf,
    check_login: bool,
}

fn args() -> Args {
    let mut out = Args { settings_file: PathBuf::from(".env"), check_login: false };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--env-file" => {
                if let Some(v) = it.next() {
                    out.settings_file = PathBuf::from(v);
                }
            }
            "--check-login" => out.check_login = true,
            _ => {}
        }
    }
    out
}

fn now_unix() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
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
    println!("athena-terminal (nothing to run yet)");
}
