from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from athena.llm.errors import ModelNotAllowed

OPENROUTER_FREE_ROUTER = "openrouter/free"
OPENAI_CHEAP_MODELS = (
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "gpt-5-nano",
    "gpt-5-mini",
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4o-mini",
)


@dataclass(frozen=True)
class ModelPolicy:
    """Which model ids a provider may be asked for. `check` raises before any request is built."""

    provider: str
    allowed: frozenset[str] | None = None  # None = any id passes the allowlist
    free_only: bool = False

    def allows(self, model: str) -> bool:
        if self.free_only and not (model.endswith(":free") or model == OPENROUTER_FREE_ROUTER):
            return False
        return self.allowed is None or model in self.allowed

    def check(self, model: str) -> None:
        if not self.allows(model):
            rule = "free models only (id must end in ':free')" if self.free_only else "an allowlist of cheap models"
            raise ModelNotAllowed(f"{self.provider}: model {model!r} is not permitted; policy is {rule}")


def allow_all(provider: str) -> ModelPolicy:
    return ModelPolicy(provider)


def free_only(provider: str) -> ModelPolicy:
    return ModelPolicy(provider, free_only=True)


def allowlist(provider: str, models: Iterable[str]) -> ModelPolicy:
    return ModelPolicy(provider, allowed=frozenset(models))
