# Assistant access policy

- Do not read, search within, edit, create, delete, copy, or print environment
  files: `.env`, `.env.*` (including templates/examples), `*.env`, and `.envrc`,
  at any depth. Do not inspect exported credential values either.
- Scope reads to named source files. Every shell content search must include
  `--glob '!**/.env*' --glob '!**/*.env' --glob '!**/.envrc'` when using `rg`.
  Do not use broad file reads, wildcard reads, or scripts to bypass this policy.
- Do not make live LLM calls during the November 12 audit; credentials are
  exhausted. Offline fakes and public repository reads are fine.
- `scripts/block_env_access.py` enforces common Claude Code tool paths through
  the local `.claude/settings.json` PreToolUse registration.
  Codex follows this instruction file; the Claude hook does not intercept Codex
  tools. Neither mechanism is an operating-system file sandbox.
