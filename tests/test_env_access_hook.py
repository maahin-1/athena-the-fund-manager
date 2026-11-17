"""Synthetic hook payloads only: never access an environment file."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts/block_env_access.py"
spec = importlib.util.spec_from_file_location("env_access_hook", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


@pytest.mark.parametrize("tool,arguments", [
    ("Read", {"file_path": "C:/work/.env"}),
    ("Read", {"file_path": "nested/.env.example"}),
    ("Edit", {"file_path": "nested/production.env"}),
    ("Write", {"file_path": "nested/.env.local"}),
    ("Read", {"file_path": "nested/.env/secrets.txt"}),
    ("Grep", {"pattern": "KEY", "path": "."}),
    ("Grep", {"pattern": "KEY", "path": "nested/.env"}),
    ("Glob", {"pattern": "**/.env*"}),
    ("Bash", {"command": "cat nested/.env"}),
    ("PowerShell", {"command": "Get-Content .e*"}),
    ("Bash", {"command": "rg KEY ."}),
    ("PowerShell", {"command": "Set-Content .env.local value"}),
    ("Bash", {"command": "rm .env"}),
    ("PowerShell", {"command": "findstr /s SECRET *"}),
    ("PowerShell", {"command": "Get-ChildItem -Force -Recurse -File | Get-Content"}),
    ("PowerShell", {"command": "gc .e*"}),
    ("PowerShell", {"command": "Get-Content .e[n]v"}),
])
def test_blocks_protected_access(tool, arguments):
    assert hook.blocked_reason({"tool_name": tool, "tool_input": arguments})


@pytest.mark.parametrize("tool,arguments", [
    ("Read", {"file_path": "src/athena/llm/envfile.py"}),
    ("Grep", {"pattern": "budget", "path": "src/athena/llm/budget.py"}),
    ("Bash", {"command": "rg --glob '!**/.env*' --glob '!**/*.env' --glob '!**/.envrc' budget src"}),
    ("Bash", {"command": "python -m pytest tests/test_contracts.py"}),
])
def test_allows_scoped_source_work(tool, arguments):
    assert hook.blocked_reason({"tool_name": tool, "tool_input": arguments}) is None


def test_cli_denial_and_invalid_input():
    for payload in ('{"tool_name":"Read","tool_input":{"file_path":".env"}}', "invalid"):
        result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
