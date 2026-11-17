# Independent T6 closure evidence - 17 November 2026

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

This minimal offline Cargo crate copies bounded.rs and its fake test fixtures, plus twelve saved reviewer probes. Copied recursive temp-directory deletion is replaced with a fresh-directory assertion.

- review-test.log: 12 saved reviewer probes pass, exit 0.
- existing-fake-tests.log: 95 existing fake tests pass, exit 0.
- collected-tests.log / test-ids.txt: 109 collected tests.
- summary.json: counts, disposition and source hash.
- src/bounded.rs: source snapshot.
- src/bounded_tests.rs: copied fixtures and appended reviewer probes.
- Full review: ../../WP07-T6-CLOSURE-2026-11-17.md.

Commands from this directory:

cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review review_
cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review -- --skip review_ --skip a_real_command_that_overruns_is_ended_and_really_gone_before_its_place_is_free --skip a_real_launch_held_at_the_gap_when_shutdown_begins_is_ended_and_counted_by_that_shutdown
cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review -- --list

Both real-Python tests are explicitly excluded; no silent bypass is counted as success. No production stores, application workers or real Python children are used. The full 414-test project suite and mutations were not independently rerun.
