# Environment-file access guard

The user requested no assistant reads, searches or writes of environment files during the audit. No such file was opened or modified. `scripts/block_env_access.py` inspects synthetic tool arguments only; it never opens the named target.

Protected names include `.env`, `.env.*` including examples/templates, `*.env`, and `.envrc`, including nested paths. The guard denies common explicit file operations, recognised shell wildcard operations, pipeline-fed PowerShell reads, direct shell paths and searches with recognised search commands. This is intentionally conservative, not complete shell analysis. Source work can use:

```powershell
rg --glob '!**/.env*' --glob '!**/*.env' --glob '!**/.envrc' PATTERN src tests
```

The existing local `.claude/settings.json` was preserved and its hook registration/deny rules strengthened. The registration now runs:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Read|Edit|Write|MultiEdit|NotebookEdit|Glob|Grep|Bash|PowerShell",
      "hooks": [{
        "type": "command",
        "command": "python \"$CLAUDE_PROJECT_DIR/scripts/block_env_access.py\""
      }]
    }]
  }
}
```

This excerpt is for merging into another installation, not replacing an existing settings file. `.claude/` is ignored by this repository, so the local registration is not automatically shared by a clone. The hook source, tests and root `AGENTS.md` are reviewable repository additions. Claude's project-settings scope is described in its [official settings documentation](https://code.claude.com/docs/en/settings).

**Coverage boundary:** this is a Claude Code hook. It does not intercept Codex's tools. Codex follows root `AGENTS.md`, which records the same prohibition. Synthetic tests establish the script's decisions, not that an active Claude session has reloaded the registration. Check/reload the hook settings in Claude Code before relying on them in that host.

It is not an operating-system sandbox: arbitrary programs, obfuscated/computed paths, symlinks, command composition and already exported credentials are not comprehensively controlled by string inspection. Strong host-level file denials would be required for adversarial enforcement. The assistant policy prohibits bypassing the guard.

Verification: `python -m pytest tests/test_env_access_hook.py -q -p no:cacheprovider` — **22 passed**, using only fake paths and JSON payloads. Regression cases include Windows `findstr`, `gc`, bracket wildcards and recursive pipeline reads.
