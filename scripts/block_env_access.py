"""Claude PreToolUse guard; never opens targets. Not an OS sandbox.

Protects templates too. Computed paths, symlinks, arbitrary programs and
already exported secrets still need host enforcement. Codex uses AGENTS.md.
"""
from __future__ import annotations
import json
import re
import sys

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep"}
SHELL_TOOLS = {"Bash", "PowerShell", "exec_command"}
TOKEN = re.compile(r"[^\s\"'`;|&()<>=,]*\.env[^\s\"'`;|&()<>=,]*", re.I)
SEARCH = re.compile(r"\b(?:rg|grep|egrep|fgrep|findstr|Select-String)\b", re.I)
EXCLUSIONS = ("!**/.env*", "!**/*.env", "!**/.envrc")


def is_secret_env_name(path: str) -> bool:
    parts = re.split(r"[\\/]", path.lower().rstrip("/\\"))
    return any(p.startswith(".env")
               or p.endswith(".env") for p in parts)


def blocked_reason(payload: dict) -> str | None:
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return "unrecognised tool input"
    if tool in FILE_TOOLS:
        for field in ("file_path", "path", "notebook_path", "glob", "pattern"):
            if any(is_secret_env_name(t) for t in TOKEN.findall(str(args.get(field, "")))):
                return "environment-file path or pattern"
        if tool == "Grep":
            path = str(args.get("path", ""))
            if not re.search(r"\.(?:py|rs|ts|tsx|md|toml|css|html)$", path, re.I):
                return "broad Grep; use rg with env-file exclusions"
    if tool in SHELL_TOOLS:
        command = str(args.get("command", args.get("cmd", "")))
        searching = bool(SEARCH.search(command))
        for token in TOKEN.findall(command):
            if searching and token in EXCLUSIONS:
                continue
            if is_secret_env_name(token):
                return "shell command referencing an environment file"
        if searching and not all(exclusion in command for exclusion in EXCLUSIONS):
            return "search without all env-file exclusions"
        if re.search(r"\|\s*(?:Get-Content|gc|cat|type)\b", command, re.I):
            return "pipeline-fed file read; use explicit non-env paths"
        if re.search(r"\b(?:cat|type|gc|sc|ac|ri|mi|ci|Get-Content|Set-Content|Add-Content|Out-File|rm|Remove-Item|cp|Copy-Item|mv|Move-Item)\b[^;|\n]*[?*\[\]]", command, re.I):
            return "wildcard file access; use explicit non-env paths"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        reason = blocked_reason(payload) if isinstance(payload, dict) else "invalid hook payload"
    except (ValueError, TypeError):
        reason = "invalid hook payload"
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Blocked: " + reason + ". Env files (including templates) are off limits. "
            "Search source with rg --glob '!**/.env*' --glob '!**/*.env' --glob '!**/.envrc'."
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
