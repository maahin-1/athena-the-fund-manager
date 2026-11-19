# Contributing

Athena is a research tool for Indian-market analysis. It does not give investment advice and no live trading is needed to work on it. Small, well-evidenced changes are the easiest to review.

## Before you start

- **Never put credentials or private data into the repository, an issue, a pull request or a test.** Tests use synthetic fixtures and fakes. Do not create credential or environment files to make a test pass; if a test seems to need one, the test is wrong, so open an issue.
- Do not add runtime data (anything under `.athena/`, `logs/`, `*.duckdb`), build output (`terminal/target`, `terminal/web/dist`, `node_modules`, caches) or large datasets. CI rejects them.
- Security problems are not reported in public: see [SECURITY.md](SECURITY.md).
- The project is MIT-licensed. There is no CLA and no mandatory sign-off; by contributing you agree your change is under the same licence.

## Tools

Python 3.11 or newer (3.13 is what the maintainers use), Rust (the version pinned in `.github/workflows/ci.yml`), Node (same), and Git for Windows (the Rust tests need `sh`). The project is built for Windows; Linux and macOS are not claimed to work.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c ci/python-constraints.txt -e ".[dev]" pyyaml
```

## Checking your change

Run the smallest check that covers your change while you work, then the full local gate before you open a pull request:

| What changed | Command (from the repository root) |
|---|---|
| Python | `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/<the test files>` |
| Everything, Python only | `.\.venv\Scripts\python.exe scripts/check_all.py --fast` |
| Rust | in `terminal/`: `cargo test --locked` |
| Web | in `terminal/web/`: `npm ci`, then `npm test` and `npm run build` |
| Full gate as CI runs it | the scripts named in `.github/workflows/ci.yml` (`docs/audit-2026-11-12/git-prep/verify/`) |

Read the results, do not just look for green: a skipped test, a test that returns early, a filter that matched nothing and a test that passed on a retry are not verification. If you add a test, make sure it **fails without your change**.

If the number of tests changes, update `ci/expected-counts.json` in the same pull request and say why in the description. A count that moves for no stated reason fails the gate on purpose. Do not add a test to the excluded list (`docs/audit-2026-11-12/git-prep/verify/python-exclusions.json`) to make the gate pass; changes to that list need maintainer review.

## Pull requests

- Branch names: `feature/...`, `fix/...`, `docs/...`, `chore/...` (and `codex/...` for changes made by the Codex agent). Branch from an up-to-date `main`.
- Keep a pull request to one coherent change. Commits should be small and each should leave the tests passing.
- Fill in the template: the problem, the final behaviour, the test that proves it, what is **not** covered, any compatibility or data effect, and how to roll back.
- Change behaviour, change the documentation next to it.
- Line endings follow `.gitattributes` (LF for source and documents, CRLF for PowerShell in checkouts). Do not reformat files you did not change.
- Dependency updates are normal pull requests: same checks, same review. A bot opening one does not make it safe.
- Anything under `.github/`, dependency manifests and locks, the scripts, broker and order-execution code, accounting and storage code, and the audit documents needs the maintainer's review (`.github/CODEOWNERS`).

## Reviewing

Reviewers look for: a test that really exercises the change, no weakening of an existing check, no new network or credential use, and an honest description of what is not verified. A reviewer who is also the author is not an independent review; say so in the pull request if that is the situation.

## Conduct

Be respectful. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
