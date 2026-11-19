//! Saved layouts: one JSON file per name under a single folder. A name can never reach outside that folder.
use std::path::PathBuf;

pub const MAX_BYTES: usize = 200 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub enum LayoutError {
    BadName,
    NotAnObject,
    TooBig,
    Missing,
    Io,
}

/// 1 to 40 letters, digits, spaces, underscores or hyphens.
pub fn name_ok(name: &str) -> bool {
    !name.is_empty() && name.chars().count() <= 40 && name.chars().all(|c| c.is_ascii_alphanumeric() || c == ' ' || c == '_' || c == '-')
}

pub struct LayoutStore {
    dir: PathBuf,
}

impl LayoutStore {
    pub fn new(dir: PathBuf) -> Self {
        LayoutStore { dir }
    }

    fn path(&self, name: &str) -> Result<PathBuf, LayoutError> {
        if name_ok(name) {
            Ok(self.dir.join(format!("{name}.json")))
        } else {
            Err(LayoutError::BadName)
        }
    }

    pub fn list(&self) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(&self.dir)
            .map(|rd| {
                rd.filter_map(Result::ok)
                    .filter_map(|e| e.file_name().to_str()?.strip_suffix(".json").map(String::from))
                    .filter(|n| name_ok(n))
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        names
    }

    pub fn get(&self, name: &str) -> Result<String, LayoutError> {
        std::fs::read_to_string(self.path(name)?).map_err(|_| LayoutError::Missing)
    }

    pub fn put(&self, name: &str, body: &[u8]) -> Result<(), LayoutError> {
        let path = self.path(name)?;
        if body.len() > MAX_BYTES {
            return Err(LayoutError::TooBig);
        }
        match serde_json::from_slice::<serde_json::Value>(body) {
            Ok(v) if v.is_object() => {}
            _ => return Err(LayoutError::NotAnObject),
        }
        std::fs::create_dir_all(&self.dir).map_err(|_| LayoutError::Io)?;
        std::fs::write(path, body).map_err(|_| LayoutError::Io)
    }

    pub fn delete(&self, name: &str) -> Result<(), LayoutError> {
        std::fs::remove_file(self.path(name)?).map_err(|_| LayoutError::Missing)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(tag: &str) -> LayoutStore {
        let dir = std::env::temp_dir().join(format!("athena-layouts-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        LayoutStore::new(dir)
    }

    #[test]
    fn names_are_checked() {
        for good in ["Mine-1", "a b", "_last", "A"] {
            assert!(name_ok(good), "{good}");
        }
        for bad in ["../x", "a/b", "a\\b", "", &"x".repeat(41), "a.json", "é"] {
            assert!(!name_ok(bad), "{bad}");
        }
    }

    #[test]
    fn put_list_get_delete_round_trip() {
        let s = store("rt");
        assert!(s.list().is_empty());
        s.put("Mine-1", br#"{"version":1}"#).unwrap();
        s.put("Another", br#"{"version":1,"x":2}"#).unwrap();
        assert_eq!(s.list(), vec!["Another", "Mine-1"]);
        assert_eq!(s.get("Mine-1").unwrap(), r#"{"version":1}"#);
        s.delete("Mine-1").unwrap();
        assert_eq!(s.get("Mine-1"), Err(LayoutError::Missing));
        assert_eq!(s.delete("Mine-1"), Err(LayoutError::Missing));
    }

    #[test]
    fn bodies_must_be_small_json_objects() {
        let s = store("body");
        assert_eq!(s.put("x", b"[1,2]"), Err(LayoutError::NotAnObject));
        assert_eq!(s.put("x", b"not json"), Err(LayoutError::NotAnObject));
        let big = format!("{{\"a\":\"{}\"}}", "z".repeat(MAX_BYTES));
        assert_eq!(s.put("x", big.as_bytes()), Err(LayoutError::TooBig));
        assert_eq!(s.put("../evil", b"{}"), Err(LayoutError::BadName));
    }
}
