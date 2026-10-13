//! Six-digit time codes (RFC 6238, SHA-1, 30-second step) from a base32 secret. The error never repeats the secret.
use hmac::{Hmac, Mac};
use sha1::Sha1;

#[derive(Debug)]
pub struct TotpError;

impl std::fmt::Display for TotpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "the TOTP secret is not valid base32")
    }
}

impl std::error::Error for TotpError {}

fn base32(input: &str) -> Result<Vec<u8>, TotpError> {
    let mut bits = 0u32;
    let mut have = 0u32;
    let mut out = Vec::new();
    for ch in input.chars().filter(|c| !c.is_whitespace() && *c != '=') {
        let v = match ch.to_ascii_uppercase() {
            c @ 'A'..='Z' => c as u32 - 'A' as u32,
            c @ '2'..='7' => c as u32 - '2' as u32 + 26,
            _ => return Err(TotpError),
        };
        bits = (bits << 5) | v;
        have += 5;
        if have >= 8 {
            have -= 8;
            out.push((bits >> have) as u8);
            bits &= (1 << have) - 1;
        }
    }
    if out.is_empty() {
        Err(TotpError)
    } else {
        Ok(out)
    }
}

pub fn code(secret_base32: &str, unix_seconds: u64) -> Result<String, TotpError> {
    let key = base32(secret_base32)?;
    let mut mac = Hmac::<Sha1>::new_from_slice(&key).map_err(|_| TotpError)?;
    mac.update(&(unix_seconds / 30).to_be_bytes());
    let h = mac.finalize().into_bytes();
    let o = (h[19] & 0x0f) as usize;
    let n = ((h[o] as u32 & 0x7f) << 24) | ((h[o + 1] as u32) << 16) | ((h[o + 2] as u32) << 8) | h[o + 3] as u32;
    Ok(format!("{:06}", n % 1_000_000))
}

#[cfg(test)]
mod tests {
    use super::*;
    const SECRET: &str = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";

    #[test]
    fn rfc6238_vectors() {
        assert_eq!(code(SECRET, 59).unwrap(), "287082");
        assert_eq!(code(SECRET, 1111111109).unwrap(), "081804");
        assert_eq!(code(SECRET, 1234567890).unwrap(), "005924");
        assert_eq!(code(SECRET, 2000000000).unwrap(), "279037");
    }

    #[test]
    fn spaces_lowercase_and_padding_are_accepted() {
        assert_eq!(code("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", 59).unwrap(), "287082");
        assert_eq!(code("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ====", 59).unwrap(), "287082");
    }

    #[test]
    fn a_bad_secret_is_an_error_that_does_not_echo_it() {
        let err = code("not base32 !!", 59).unwrap_err();
        assert!(!format!("{err}").contains("not base32"));
    }
}
