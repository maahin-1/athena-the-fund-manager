from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

import requests

from athena.llm.errors import ProviderError

RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
# a 429 that means "your account has no quota or credits" is not a rate limit: waiting and asking again three times only wastes seconds, and the model is unusable until the owner acts
QUOTA_MARKS = ("insufficient_quota", "no credits", "exceeded your current quota", "out of credits", "billing", "payment required")


class OpenAICompatibleClient:
    """One model on one OpenAI-compatible chat-completions endpoint (NVIDIA NIM, OpenRouter, OpenAI).

    Satisfies the `LLMClient` protocol."""

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        token_param: str = "max_tokens",
        send_temperature: bool = True,
        max_tokens: int = 3000,
        timeout: float = 60.0,
        retries: int = 2,
        post: Callable[..., Any] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = provider
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.token_param = token_param
        self.send_temperature = send_temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        self._post = post
        self._sleep = sleep
        self.calls = 0
        self.last_usage: dict[str, int] = {}  # tokens the provider says the last reply used: prompt, completion (reasoning included) and total
        self.last_attempts: list[dict[str, Any]] = []  # the HTTP requests the last `complete` made, each with its outcome (see `_attempt`)

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

    def _begin(self, n: int) -> dict[str, Any]:
        """Record a provider request as it is about to be sent, as `sent` with nothing known of its end. It is settled by `_end`; anything that stops the call in between (a reply of a shape nobody expected,
        an interruption after the request left) leaves it `sent`, which the ledger counts. The history is therefore never empty once a request may have gone out."""
        entry: dict[str, Any] = {"n": n, "model": self.name, "outcome": "sent", "status": None, "retry": n > 1, "detail": ""}
        self.last_attempts.append(entry)
        return entry

    def _end(self, entry: dict[str, Any], outcome: str, status: int | None = None, detail: str = "") -> None:
        """Say how a provider request ended: how, the HTTP status when there was an answer, and (scrubbed) what the provider said. Attempt 2 and later are retries."""
        entry.update(outcome=outcome, status=status, detail=self._scrub(detail)[:160] if detail else "")

    def complete(self, system: str, user: str) -> str:
        failure: ProviderError | None = None
        self.last_attempts = []  # one entry per HTTP request this call makes, so a caller can tell a request the provider declined from one that may have been processed
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(2.0**attempt)
            self.calls += 1
            sent = self._begin(attempt + 1)
            try:
                response = self._post(
                    self.url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=self._payload(system, user),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                failure = ProviderError(f"{self.name}: network error {type(exc).__name__}", retryable=True, timed_out=isinstance(exc, requests.Timeout))
                self._end(sent, "timeout" if isinstance(exc, requests.Timeout) else "network_error", None, type(exc).__name__)
                continue
            if response.status_code == 200:
                try:
                    reply = self._read(response)
                    self._end(sent, "ok", 200)
                    return reply
                except ProviderError as exc:  # OpenRouter can answer 200 with an error body
                    self._end(sent, "bad_reply", 200, str(exc))
                    if not exc.retryable:
                        raise
                    failure = exc
                    continue
            retryable = response.status_code in RETRYABLE_STATUSES and not (response.status_code in (402, 429) and any(m in str(response.text).lower() for m in QUOTA_MARKS))
            failure = ProviderError(
                f"{self.name}: HTTP {response.status_code} {self._scrub(response.text)}",
                status=response.status_code,
                retryable=retryable,
            )
            self._end(sent, "http_error", response.status_code, str(response.text))
            if not retryable:
                raise failure
        assert failure is not None
        raise failure

    def _bad(self, why: str) -> ProviderError:
        return ProviderError(f"{self.name}: unusable reply ({why})", retryable=True)

    def _read(self, response: Any) -> str:
        """The reply text of a 200 answer. Every shape that is not a usable reply is a `ProviderError` (never a `KeyError` or `TypeError` that would leave the request unrecorded). Token usage is taken only
        when it is well-formed; otherwise it stays unreported."""
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
        if not isinstance(choices, list) or not isinstance(choices[0], dict):
            raise self._bad("choices is not a list of objects")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise self._bad("the first choice has no message object")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise self._bad(f"message content is a {type(content).__name__}, not text")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        self.last_usage = {k: int(usage[k]) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                           if isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool) and math.isfinite(usage[k]) and usage[k] >= 0}
        return content or ""
