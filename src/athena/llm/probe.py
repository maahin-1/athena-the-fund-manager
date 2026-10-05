from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from athena.llm.client import OpenAICompatibleClient
from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting
from athena.llm.router import PROVIDERS, TIERS

PROBE_SYSTEM = "You are a connectivity check."
PROBE_USER = "Reply with the single word OK."
PROBE_MAX_TOKENS = 400  # reasoning models spend part of this thinking before they answer


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    model: str
    ok: bool
    detail: str
    seconds: float


def probe_models(
    env_file: Path | str = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] = requests.post,
) -> list[ProbeResult]:
    """One tiny request per distinct (provider, model) in the tiers whose key is set. Never shows a key."""
    results: list[ProbeResult] = []
    seen: set[tuple[str, str]] = set()
    for entries in TIERS.values():
        for provider, model in entries:
            if (provider, model) in seen:
                continue
            seen.add((provider, model))
            spec = PROVIDERS[provider]
            key = read_setting(spec.key_name, env_file, environ)
            if not key:
                continue
            client = OpenAICompatibleClient(
                provider, model, spec.base_url, key, spec.policy,
                token_param=spec.token_param, send_temperature=spec.send_temperature,
                max_tokens=PROBE_MAX_TOKENS, timeout=60.0, retries=0, post=post,
            )
            started = time.time()
            try:
                reply = client.complete(PROBE_SYSTEM, PROBE_USER).strip()
                ok, detail = bool(reply), (reply[:40] or "empty reply")
            except Exception as exc:  # report every failure kind, never crash the probe
                ok, detail = False, f"{type(exc).__name__}: {str(exc)[:110]}"
            results.append(ProbeResult(provider, model, ok, detail, round(time.time() - started, 1)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check which configured LLM models this account can call.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    args = parser.parse_args(argv)
    results = probe_models(env_file=args.env_file)
    if not results:
        print("no provider key is set")
        return 1
    for r in results:
        print(f"{'OK  ' if r.ok else 'FAIL'} {r.provider:10s} {r.model:45s} {r.seconds:5.1f}s  {r.detail}")
    return 0 if all(r.ok for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
