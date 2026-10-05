from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests

from athena.llm.errors import ProviderError
from athena.llm.policy import ModelPolicy
from athena.llm.spend import SpendGuard

RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleClient:
    """One model on one OpenAI-compatible chat-completions endpoint (NVIDIA NIM, OpenRouter, OpenAI).

    Satisfies the `LLMClient` protocol. The model policy is checked when the client is built and again
    before every request; the spend guard (if any) is checked before the request and updated from the
    reported token usage after it."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        policy: ModelPolicy,
        guard: SpendGuard | None = None,
        token_param: str = "max_tokens",
        send_temperature: bool = True,
        max_tokens: int = 3000,
        timeout: float = 60.0,
        retries: int = 2,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ):
        policy.check(model)
        self.provider = provider
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.policy = policy
        self.guard = guard
        self.token_param = token_param
        self.send_temperature = send_temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        self._post = post
        self._sleep = sleep
        self.calls = 0

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    def _scrub(self, text: str) -> str:
        return text.replace(self._api_key, "<key>")[:160] if self._api_key else text[:160]

    def _payload(self, system: str, user: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            self.token_param: self.max_tokens,
        }
        if self.send_temperature:
            payload["temperature"] = 0
        return payload

    def complete(self, system: str, user: str) -> str:
        self.policy.check(self.model)
        if self.guard is not None:
            self.guard.check(self.guard.estimate(len(system) + len(user), self.max_tokens))
        failure: ProviderError | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(2.0**attempt)
            self.calls += 1
            try:
                response = self._post(
                    self.url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=self._payload(system, user),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                failure = ProviderError(f"{self.name}: network error {type(exc).__name__}", retryable=True)
                continue
            if response.status_code == 200:
                try:
                    return self._read(response, system, user)
                except ProviderError as exc:  # OpenRouter can answer 200 with an error body
                    if not exc.retryable:
                        raise
                    failure = exc
                    continue
            retryable = response.status_code in RETRYABLE_STATUSES
            failure = ProviderError(
                f"{self.name}: HTTP {response.status_code} {self._scrub(response.text)}",
                status=response.status_code,
                retryable=retryable,
            )
            if not retryable:
                raise failure
        assert failure is not None
        raise failure

    def _read(self, response: Any, system: str, user: str) -> str:
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name}: reply was not JSON", retryable=True) from exc
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices:
            error = body.get("error") if isinstance(body, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            status = code if isinstance(code, int) else None
            raise ProviderError(
                f"{self.name}: no choices in the reply ({self._scrub(str(error or body))})",
                status=status,
                retryable=status is None or status in RETRYABLE_STATUSES,
            )
        if self.guard is not None:
            usage = body.get("usage") or {}
            if "prompt_tokens" in usage and "completion_tokens" in usage:
                self.guard.record(int(usage["prompt_tokens"]), int(usage["completion_tokens"]))
            else:  # no usage reported: charge the worst case rather than nothing
                self.guard.record(-(-(len(system) + len(user)) // 3), self.max_tokens)
        return choices[0]["message"].get("content") or ""
