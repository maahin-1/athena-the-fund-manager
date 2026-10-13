//! Log in to Angel One for market data. Read-only use: nothing here places an order. No PIN, secret or token is ever shown.
use std::fmt;
use std::time::Duration;

use serde_json::{json, Value};

use crate::config::Settings;
use crate::totp;

const LOGIN_URL: &str = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword";

#[derive(Clone)]
pub struct Session {
    pub api_key: String,
    pub client_code: String,
    pub jwt: String,
    pub feed_token: String,
}

impl fmt::Debug for Session {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Session(client_code={:?})", self.client_code)
    }
}

#[derive(Debug)]
pub enum LoginError {
    Refused(String),
    NoFeedToken,
    Request(&'static str),
    Totp,
}

impl fmt::Display for LoginError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LoginError::Refused(m) => write!(f, "Angel One refused the login: {m}"),
            LoginError::NoFeedToken => write!(f, "Angel One gave no feed token"),
            LoginError::Request(kind) => write!(f, "the login request failed: {kind}"),
            LoginError::Totp => write!(f, "the TOTP secret is not valid base32"),
        }
    }
}

impl std::error::Error for LoginError {}

pub fn build_request(settings: &Settings, code: &str) -> Value {
    json!({"clientcode": settings.client_code, "password": settings.pin, "totp": code})
}

/// `(jwt, feed_token)` from the broker's reply, or an error holding only the broker's short message.
pub fn parse_reply(body: &Value) -> Result<(String, String), LoginError> {
    if body.get("status").and_then(Value::as_bool) != Some(true) {
        let message: String = body.get("message").and_then(Value::as_str).unwrap_or("no reason given").chars().take(120).collect();
        return Err(LoginError::Refused(message));
    }
    let data = body.get("data").unwrap_or(&Value::Null);
    let jwt = data.get("jwtToken").and_then(Value::as_str).unwrap_or("");
    let feed = data.get("feedToken").and_then(Value::as_str).unwrap_or("");
    if jwt.is_empty() || feed.is_empty() {
        return Err(LoginError::NoFeedToken);
    }
    Ok((jwt.to_string(), feed.to_string()))
}

pub async fn login(client: &reqwest::Client, settings: &Settings, unix: u64) -> Result<Session, LoginError> {
    let code = totp::code(&settings.totp_secret, unix).map_err(|_| LoginError::Totp)?;
    let reply = client
        .post(LOGIN_URL)
        .timeout(Duration::from_secs(15))
        .header("Content-type", "application/json")
        .header("Accept", "application/json")
        .header("X-UserType", "USER")
        .header("X-SourceID", "WEB")
        .header("X-ClientLocalIP", "127.0.0.1")
        .header("X-ClientPublicIP", "127.0.0.1")
        .header("X-MACAddress", "00:00:00:00:00:00")
        .header("X-PrivateKey", &settings.api_key)
        .json(&build_request(settings, &code))
        .send()
        .await
        .map_err(|e| LoginError::Request(if e.is_timeout() { "timeout" } else if e.is_connect() { "connect" } else { "other" }))?;
    let body: Value = reply.json().await.map_err(|_| LoginError::Request("unreadable reply"))?;
    let (jwt, feed_token) = parse_reply(&body)?;
    Ok(Session { api_key: settings.api_key.clone(), client_code: settings.client_code.clone(), jwt, feed_token })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn body_has_the_three_fields() {
        let s = Settings::for_test("K", "C1", "1234", "AAAA");
        assert_eq!(build_request(&s, "123456"), json!({"clientcode": "C1", "password": "1234", "totp": "123456"}));
    }

    #[test]
    fn reply_gives_tokens() {
        let r = json!({"status": true, "data": {"jwtToken": "J", "refreshToken": "R", "feedToken": "F"}});
        assert_eq!(parse_reply(&r).unwrap(), ("J".to_string(), "F".to_string()));
    }

    #[test]
    fn refusal_carries_only_a_short_message() {
        let r = json!({"status": false, "message": "Invalid totp"});
        assert_eq!(parse_reply(&r).unwrap_err().to_string(), "Angel One refused the login: Invalid totp");
        let long = "x".repeat(500);
        let r = json!({"status": false, "message": long});
        assert!(parse_reply(&r).unwrap_err().to_string().len() < 160);
    }

    #[test]
    fn missing_feed_token_is_an_error() {
        let r = json!({"status": true, "data": {"jwtToken": "J"}});
        assert!(parse_reply(&r).is_err());
    }

    #[test]
    fn session_debug_hides_tokens() {
        let s = Session { api_key: "KEYSECRET".into(), client_code: "C1".into(), jwt: "JJJ".into(), feed_token: "FFF".into() };
        let t = format!("{s:?}");
        assert!(t.contains("C1") && !t.contains("JJJ") && !t.contains("FFF") && !t.contains("KEYSECRET"));
    }
}
