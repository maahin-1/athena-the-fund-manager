//! The settings file, read for the four Angel One names only. Values are never shown: `Debug` prints the client code and nothing else.
use std::fmt;
use std::path::Path;

const NAMES: [&str; 4] = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"];

#[derive(Clone)]
pub struct Settings {
    pub api_key: String,
    pub client_code: String,
    pub pin: String,
    pub totp_secret: String,
}

impl fmt::Debug for Settings {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Settings(client_code={:?})", self.client_code)
    }
}

#[derive(Debug)]
pub enum ConfigError {
    /// Names (never values) of the settings that are absent or empty.
    Missing(Vec<&'static str>),
    Unreadable,
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::Missing(names) => write!(f, "missing in the settings file: {}", names.join(", ")),
            ConfigError::Unreadable => write!(f, "the settings file could not be read"),
        }
    }
}

impl std::error::Error for ConfigError {}

fn unquote(value: &str) -> &str {
    let v = value.trim();
    for q in ['"', '\''] {
        if v.len() >= 2 && v.starts_with(q) && v.ends_with(q) {
            return &v[1..v.len() - 1];
        }
    }
    v
}

pub fn parse(text: &str) -> Result<Settings, ConfigError> {
    let mut found: [Option<String>; 4] = Default::default();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((name, value)) = line.split_once('=') {
            if let Some(i) = NAMES.iter().position(|n| *n == name.trim()) {
                let v = unquote(value).to_string();
                if !v.is_empty() {
                    found[i] = Some(v);
                }
            }
        }
    }
    let missing: Vec<&'static str> = NAMES.iter().zip(found.iter()).filter(|(_, v)| v.is_none()).map(|(n, _)| *n).collect();
    if !missing.is_empty() {
        return Err(ConfigError::Missing(missing));
    }
    let [a, b, c, d] = found;
    Ok(Settings { api_key: a.unwrap(), client_code: b.unwrap(), pin: c.unwrap(), totp_secret: d.unwrap() })
}

pub fn read(path: &Path) -> Result<Settings, ConfigError> {
    let text = std::fs::read_to_string(path).map_err(|_| ConfigError::Unreadable)?;
    parse(&text)
}

#[cfg(test)]
impl Settings {
    pub fn for_test(api_key: &str, client_code: &str, pin: &str, totp_secret: &str) -> Self {
        Settings { api_key: api_key.into(), client_code: client_code.into(), pin: pin.into(), totp_secret: totp_secret.into() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_four_names_and_debug_hides_values() {
        let text = "ANGEL_API_KEY=k1\nANGEL_CLIENT_CODE=C123\nANGEL_PIN=9999\nANGEL_TOTP_SECRET=JBSWY3DPEHPK3PXP\nOTHER=x\n";
        let s = parse(text).unwrap();
        assert_eq!(s.client_code, "C123");
        let shown = format!("{s:?}");
        assert!(shown.contains("C123"));
        for hidden in ["k1", "9999", "JBSWY3DPEHPK3PXP"] {
            assert!(!shown.contains(hidden), "{hidden} leaked");
        }
    }

    #[test]
    fn missing_names_are_listed_without_values() {
        let err = parse("ANGEL_API_KEY=k1\n").unwrap_err();
        match err {
            ConfigError::Missing(names) => assert_eq!(names, vec!["ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"]),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn quotes_and_comments_are_handled() {
        let text = "# c\nANGEL_API_KEY=\"k1\"\nANGEL_CLIENT_CODE='C1'\nANGEL_PIN=1\nANGEL_TOTP_SECRET=AAAA\n";
        assert_eq!(parse(text).unwrap().api_key, "k1");
    }
}
