# T6 review 3 independent artifacts

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

Source/test snapshots and a minimal offline Cargo crate. No terminal, DuckDB, production stores or real Python processes are needed.

- review-test.log: nine saved probes pass; three new probes fail (exit 101).
- existing-fake-tests.log: 88 existing fake tests pass (exit 0).
- test-ids.txt: 101 collected IDs; collected-tests.log preserves raw collection.
- summary.json: counts, disposition and source hash.
- src/bounded_tests.rs: copied test fixtures, safe fresh-directory assertions and appended reviewer probes.
- src/bounded.rs: byte-for-byte source snapshot.
- Full review: ../../WP07-T6-REVIEW3-2026-11-17.md.

Commands from this directory:

cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review review_
cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review -- --skip review_ --skip a_real_command_that_overruns_is_ended_and_really_gone_before_its_place_is_free
cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review -- --list

The existing real-Python test is explicitly filtered out. Logs from these three commands are separate files. The full 406-test project suite and Claude's mutations were not independently repeated.
