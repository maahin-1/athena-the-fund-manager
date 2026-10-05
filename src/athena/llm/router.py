from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from athena.agents.base import LLMClient
from athena.contracts import AthenaError
from athena.fallback import FallbackChain, HealthRegistry
from athena.llm.client import OpenAICompatibleClient
from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting
from athena.llm.errors import ProviderError


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    key_name: str
    token_param: str = "max_tokens"
    send_temperature: bool = True


PROVIDERS: dict[str, ProviderSpec] = {
    "nvidia": ProviderSpec("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "openrouter": ProviderSpec(
        "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"
    ),
    "openai": ProviderSpec(
        "openai", "https://api.openai.com/v1", "OPENAI_API_KEY",
        token_param="max_completion_tokens", send_temperature=False,
    ),
}

# Tier -> models in fallback order. Checked on 5 Oct 2026; NVIDIA lists more models than an account
# can call, so the chain falls through on 404 and timeouts.
TIERS: dict[str, tuple[tuple[str, str], ...]] = {
    "cheap": (
        ("nvidia", "openai/gpt-oss-20b"),
        ("openrouter", "google/gemma-4-26b-a4b-it:free"),
        ("openai", "gpt-5.4-nano"),
    ),
    "mid": (
        ("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
        ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
        ("openai", "gpt-5.4-mini"),
    ),
    "strong": (
        ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        ("openai", "gpt-5.4-mini"),
    ),
}
ROLE_TIERS = {"resolver": "cheap", "specialist": "mid", "judge": "strong"}
DEFAULT_TIER = "mid"


class FallbackLLM:
    """Tries each client in order. A model the account cannot use (401/403/404) is marked degraded and skipped
    for the rest of the session; rate limits, timeouts and empty replies just move on to the next one."""

    def __init__(self, clients: Sequence[OpenAICompatibleClient], health: HealthRegistry | None = None):
        self.clients = list(clients)
        self.health = health or HealthRegistry()
        self.last_source = ""
        self.last_failures: tuple[tuple[str, str], ...] = ()
        self._chain = FallbackChain([(client.name, self._guarded(client)) for client in self.clients])

    @property
    def names(self) -> list[str]:
        return [client.name for client in self.clients]

    def _guarded(self, client: OpenAICompatibleClient) -> Callable[[str, str], str]:
        def call(system: str, user: str) -> str:
            try:
                return client.complete(system, user)
            except ProviderError as exc:
                if not exc.retryable:
                    self.health.mark_degraded(client.name, str(exc))
                raise

        return call

    def complete(self, system: str, user: str) -> str:
        result = self._chain.run(system, user, health=self.health)
        self.last_source, self.last_failures = result.source, result.failures
        return result.value


class Router(Protocol):
    def client_for(self, role: str) -> LLMClient: ...


class StaticRouter:
    """Role -> tier -> fallback chain, from static config (TRD section 5). A decision-model router can replace it."""

    def __init__(self, tiers: Mapping[str, FallbackLLM], role_tiers: Mapping[str, str] = ROLE_TIERS):
        self._tiers = dict(tiers)
        self._role_tiers = dict(role_tiers)

    def client_for(self, role: str) -> FallbackLLM:
        tier = self._role_tiers.get(role, DEFAULT_TIER)
        if tier not in self._tiers:
            raise AthenaError(f"no models configured for tier {tier!r}")
        return self._tiers[tier]


def build_router(
    env_file: Path | str = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
    tiers: Mapping[str, Sequence[tuple[str, str]]] = TIERS,
    **client_options: object,
) -> StaticRouter:
    """Build every tier from the providers whose keys are set. Raises if no key is set at all."""
    keys = {name: read_setting(spec.key_name, env_file, environ) for name, spec in PROVIDERS.items()}
    if not any(keys.values()):
        raise AthenaError("no LLM provider key is set (NVIDIA_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY)")
    built: dict[str, FallbackLLM] = {}
    for tier, entries in tiers.items():
        clients = []
        for provider, model in entries:
            spec = PROVIDERS[provider]
            if not keys[provider]:
                continue
            clients.append(
                OpenAICompatibleClient(
                    provider, model, spec.base_url, keys[provider],
                    token_param=spec.token_param, send_temperature=spec.send_temperature,
                    **client_options,  # type: ignore[arg-type]
                )
            )
        if clients:
            built[tier] = FallbackLLM(clients)
    return StaticRouter(built)
