# Revision 2 reviewer evidence

Canonical test: test_review_regressions.py.
Results: reviewer-tests.log and reviewer-results.xml; summary.json records scope and counts.

Command from repository root (PowerShell; use a fresh basetemp directory):

.\\.venv\\Scripts\\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=<new scratch directory> --junitxml=<new result file> docs/audit-2026-11-12/git-prep/review2/test_review_regressions.py docs/audit-2026-11-12/git-prep/verify/test_compare_manifest.py

Observed: 12 passed and 3 intentionally failing regressions. Nine existing comparator tests and three controls pass; unexpected baseline deletion, nested event interpolation and hidden bad action pin fail the reviewer expectations.

No PyYAML installation was performed. Workflow fixtures are JSON documents (a valid YAML subset); a JSON parser adapter is injected only for those fixtures to exercise actual validator logic after parsing. This is not a parser/schema test. Run equivalent cases with the real parser during correction.

Synthetic Git objects/indexes are created only under the new pytest scratch directory. No commits, protected files, application workers, network calls or real workflows are used. Keep generated synthetic-cases Git metadata in archived evidence rather than ordinary source commits.

zero-origin-report.json separately demonstrates that a missing origin log with minimum zero returns ok=true. It is not a full Rust-gate execution.
