from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from athena.agents.base import LLMClient
from athena.contracts import AthenaError
from athena.fallback import FallbackChain, HealthRegistry
from athena.llm.budget import Meter, MeteredLLM
from athena.llm.cache import CachedLLM, ReplyCache
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
HEALTH_PATH = Path(".athena") / "llm_health.json"  # models found unusable, shared by every process, each entry expiring after six hours

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
        # added Oct 27, 2026: the super model answered 404 on NVIDIA and OpenRouter and the OpenAI account has no credits, so the tier had no model left; a 404 marks a model degraded and it is skipped
        ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ),
    "strong": (
        ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        ("openai", "gpt-5.4-mini"),
    ),
}
# The judge reads one evidence sheet and a debate and writes a decision, so it is worth a paid model; the owner's OpenAI credits pay for it (23 Oct 2026: gpt-5.4 is below the pro models),
# with the free strong tier behind it if OpenAI refuses. Its calls are counted and capped by `athena.llm.budget`.
TIERS["judge"] = (("openai", "gpt-5.4"), ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"), ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"))
ROLE_TIERS = {"resolver": "cheap", "specialist": "mid", "judge": "judge"}
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

    @property
    def last_usage(self) -> dict[str, int]:
        """Tokens used by the reply that answered last (empty when the provider did not say)."""
        answered = next((c for c in self.clients if c.name == self.last_source), None)
        return dict(getattr(answered, "last_usage", {}) or {})

    def complete(self, system: str, user: str) -> str:
        result = self._chain.run(system, user, health=self.health)
        self.last_source, self.last_failures = result.source, result.failures
        return result.value


class Router(Protocol):
    def client_for(self, role: str) -> LLMClient: ...


class StaticRouter:
    """Role -> tier -> fallback chain, from static config (TRD section 5). A decision-model router can replace it."""

    def __init__(self, tiers: Mapping[str, FallbackLLM], role_tiers: Mapping[str, str] = ROLE_TIERS, meter: Meter | None = None, cache: ReplyCache | None = None):
        self._tiers = dict(tiers)
        self._cache = cache  # when given, an identical prompt to the same tier within a week is answered from disk, before the meter, so a hit costs and counts nothing
        self._role_tiers = dict(role_tiers)
        self._meter = meter  # when given, every client it hands out counts its calls and stops at the owner's ceiling (`athena.llm.budget`)

    def client_for(self, role: str) -> FallbackLLM:
        tier = self._role_tiers.get(role, DEFAULT_TIER)
        if tier not in self._tiers:
            raise AthenaError(f"no models configured for tier {tier!r}")
        client: Any = MeteredLLM(self._tiers[tier], tier, self._meter) if self._meter is not None else self._tiers[tier]
        return CachedLLM(client, tier, self._cache) if self._cache is not None else client  # type: ignore[no-any-return]


def build_router(
    env_file: Path | str = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
    tiers: Mapping[str, Sequence[tuple[str, str]]] = TIERS,
    meter: Meter | None = None,
    cache: ReplyCache | None = None,
    health_path: Path | str | None = HEALTH_PATH,
    **client_options: object,
) -> StaticRouter:
    """Build every tier from the providers whose keys are set. Raises if no key is set at all."""
    keys = {name: read_setting(spec.key_name, env_file, environ) for name, spec in PROVIDERS.items()}
    if not any(keys.values()):
        raise AthenaError("no LLM provider key is set (NVIDIA_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY)")
    built: dict[str, FallbackLLM] = {}
    health = HealthRegistry(health_path)  # one list for all tiers: a model is the same model whichever tier asks
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
            built[tier] = FallbackLLM(clients, health)
    return StaticRouter(built, meter=meter, cache=cache)
