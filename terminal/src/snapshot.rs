//! Reading the warehouse while a long job is writing it.
//!
//! DuckDB lets one process write or many read, never both, so a build that holds `warehouse.duckdb` for an hour used to turn every chart and every statement into "warehouse busy". Before a job
//! that writes the warehouse starts, the supervisor (`athena.ops.supervisor`) saves a copy beside it, `warehouse.snap.duckdb`. A reader opens the real file, and when that is locked by a writer it
//! opens the copy instead: the data as it was when the job began, which is what a chart wants.
use std::path::{Path, PathBuf};

use duckdb::{AccessMode, Config, Connection};

/// `…/warehouse.duckdb` -> `…/warehouse.snap.duckdb`.
pub fn snapshot_path(path: &Path) -> PathBuf {
    path.with_extension("snap.duckdb")
}

/// Open read-only; if the file is locked by a writer, open its snapshot when there is one.
pub fn open_read_only(path: &Path) -> duckdb::Result<Connection> {
    let first = Connection::open_with_flags(path, Config::default().access_mode(AccessMode::ReadOnly)?);
    match first {
        Ok(conn) => Ok(conn),
        Err(err) => {
            let snap = snapshot_path(path);
            if snap.is_file() {
                Connection::open_with_flags(&snap, Config::default().access_mode(AccessMode::ReadOnly)?).map_err(|_| err)
            } else {
                Err(err)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make(path: &Path, close: f64) {
        Connection::open(path).unwrap().execute_batch(&format!("CREATE TABLE t (v DOUBLE); INSERT INTO t VALUES ({close}); CHECKPOINT;")).unwrap();
    }

    #[test]
    fn a_locked_file_is_read_from_its_snapshot_and_a_free_one_from_itself() {
        let dir = std::env::temp_dir().join(format!("athena-snap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let main = dir.join("w.duckdb");
        make(&main, 1.0);
        std::fs::copy(&main, snapshot_path(&main)).unwrap();
        let read = |p: &Path| open_read_only(p).unwrap().query_row("SELECT v FROM t", [], |r| r.get::<_, f64>(0)).unwrap();
        assert_eq!(read(&main), 1.0);
        {
            // a writer in this process holds the file read-write: a second read-only open of it fails, and the snapshot answers
            let writer = Connection::open(&main).unwrap();
            writer.execute_batch("UPDATE t SET v = 2.0").unwrap();
            let second = Connection::open_with_flags(&main, Config::default().access_mode(AccessMode::ReadOnly).unwrap());
            if second.is_err() {
                assert_eq!(read(&main), 1.0);
            }
        }
        std::fs::remove_file(snapshot_path(&main)).unwrap();
        assert!(open_read_only(&dir.join("absent.duckdb")).is_err(), "no file and no snapshot is still an error");
        std::fs::remove_dir_all(&dir).ok();
    }
}
