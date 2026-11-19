# Independent T6 review artifacts - 17 November 2026

> **Where the raw files are.** Raw logs, bulk result files and regenerable databases of the runs described here are not in Git history. They are kept in a verified evidence archive; every such file is listed with its path, size and SHA-256 in [EVIDENCE-INDEX.md](../../EVIDENCE-INDEX.md). Run records, summaries, collected test IDs, scripts and specifications stay beside this file.

This is an isolated Cargo crate, not the terminal. It copies bounded.rs byte-for-byte and bounded_tests.rs with recursive temporary-directory deletions changed to fresh-directory assertions, then appends reviewer probes. No production data or real Python workers are needed.

Results:
- review-test.log: 9 reviewer probes, 4 pass and 5 deliberately fail, exit 101.
- existing-fake-tests.log: 67 existing fake tests pass, exit 0.
- test-ids.txt: 77 collected IDs.
- collected-tests.log: raw collection output.
- summary.json: machine-readable disposition and counts.
- src/bounded_tests.rs: existing fake fixtures plus independently added reviewer probes at the end.
- src/bounded.rs: source snapshot at review time.
- Cargo.toml and Cargo.lock: offline minimal dependency graph.
- Full findings: ../../WP07-T6-REVIEW-2026-11-17.md (from this artifact directory, use ../../ to reach the audit folder).

Commands from this artifact directory (PowerShell; substitute a dedicated target directory if desired):

cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review review_
cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review -- --skip review_ --skip a_real_command_that_overruns_is_ended_and_really_gone_before_its_place_is_free
cargo test --offline --locked --target-dir D:/projects/learn-project/athena-the-fund-manager/terminal/target/t6r-review -- --list

The only real-Python test is explicitly excluded rather than silently passed. The nonexistent-command probe cannot launch a child. Other reviewer cases use immediately controlled fake children and synthetic journal files.
