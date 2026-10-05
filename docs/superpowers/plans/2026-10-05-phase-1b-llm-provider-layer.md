# Phase 1b — LLM Provider Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Amendment (5 Oct 2026, after execution):** at the user's request the spend guard described below (Task 2, the guard parts of Tasks 3-5, `OPENAI_SPEND_CAP_USD`, `.athena/llm_spend.json`) was removed. The model policy (OpenAI allowlist, OpenRouter `:free` only) stays; OpenAI's account balance is now the only spend limit. The text below is the plan as executed, kept as a record.

**Goal:** Give `Specialist` a real model behind the `LLMClient` protocol: NVIDIA first, OpenRouter free models second, OpenAI cheap models third, with the spending limits enforced in code so a bug or a loop cannot spend money the user did not allow.

**Architecture:** `athena.llm` holds one `OpenAICompatibleClient` (all three providers speak the OpenAI chat-completions format) wrapped by per-provider rules. A `ModelPolicy` decides which model ids a provider may be asked for and is checked when a client is built and again before every request. A persisted `SpendGuard` caps cumulative OpenAI spend and fails closed. `FallbackLLM` chains clients using the existing `FallbackChain`, skipping a model the account cannot use for the rest of the session. `StaticRouter` maps roles (resolver, specialist, judge) to tiers (cheap, mid, strong). A `probe` module reports which configured models the account can call.

**Tech Stack:** Python >= 3.11, `requests` (already a dependency), pytest. No new dependencies and no vendor SDKs.

**Spec:** `TRD.md` §5 (model routing, `Router` interface), the "Provider decision" paragraph under it, and §2.2 (`LLMClient`); `.env.example`.

**Plan series:** 0a-0e, 1a (done) -> **1b (this plan)** -> 1c (orchestrator skeleton: resolve, route, run specialists, conviction-weighted blend, debate stubbed) -> 1d (remaining equity specialists in ratio-only mode, dashboard, backtest). The earlier outline put the provider and the orchestrator in one plan; they are independent subsystems, so they are split.

**Suggested models:** Sonnet at medium effort. Prototyped end to end in a scratch copy first: 315 offline tests passed, three safety mutations were each caught by the matching test, and the live tests ran against all three real providers.

## Verified findings (5 Oct 2026)

Live probe, one tiny request per distinct model in the tiers (keys loaded inside the process, never printed):

| Provider | Model | Result |
| --- | --- | --- |
| NVIDIA | `openai/gpt-oss-20b`, `nvidia/nemotron-3-super-120b-a12b` | OK, 1 s |
| NVIDIA | `nvidia/nemotron-3-ultra-550b-a55b` | OK in 6-10 s on one run, HTTP 503 "overloaded" on another |
| OpenRouter | `nvidia/nemotron-3-super-120b-a12b:free`, `nvidia/nemotron-3-ultra-550b-a55b:free` | OK |
| OpenRouter | `google/gemma-4-26b-a4b-it:free` | HTTP 429 upstream rate limit on every run |
| OpenAI | `gpt-5.4-nano`, `gpt-5.4-mini` | OK, 2-4 s |

- **End to end:** the Quant/Technical specialist from Plan 1a, run through the router on a real SBIN technical packet, returned a valid, fully grounded output from `nvidia:nvidia/nemotron-3-super-120b-a12b`.
- **A bug the live run found:** OpenRouter sometimes answers HTTP 200 with an error body instead of `choices`. The first client version crashed with `KeyError: 'choices'`. The client now treats that as a provider error, retries it if the embedded code is retryable (429, 5xx), and gives up at once if it is not (404). Task 3 has the tests.
- **Spend:** about ten probes and one specialist run charged OpenAI roughly $0.0006 on the guard's deliberately pessimistic prices ($2.50 per million input tokens, $10 per million output tokens, flat for every model, far above real nano/mini prices). Real OpenAI prices are not tracked because they are not verified here; the pessimistic flat price makes the cap conservative instead.
- **Parameter profile:** the OpenAI requests use `max_completion_tokens` and send no `temperature`; that profile worked live. Whether `max_tokens` or `temperature: 0` would be rejected by the gpt-5.4 family was not tested.
- **Not assumed:** the free NVIDIA catalog lists 81 models but several return 404 for this account, and the free tiers rate-limit; the fallback chain and the probe exist because of that. One run of each model says nothing about answer quality; that needs the golden sets.

## Global Constraints

- No network in the default test run; the live tests are opt-in via `--live` and use the keys in `.env`.
- **Spending rules are code, not convention.** OpenRouter: only model ids ending in `:free` (or `openrouter/free`). OpenAI: only the allowlist `gpt-5.4-nano`, `gpt-5.4-mini`, `gpt-5-nano`, `gpt-5-mini`, `gpt-4.1-nano`, `gpt-4.1-mini`, `gpt-4o-mini`, plus a persisted cumulative cap (default `$1.00`, override with `OPENAI_SPEND_CAP_USD`; the account holds about $4). A disallowed model or an exceeded cap must raise before any request is sent.
- The spend guard fails closed: an unreadable spend record refuses to spend.
- API keys are read with `read_setting` and handed only to the client that needs them; they must never appear in an exception message, a log line or test output (`<key>` replaces them).
- A model the account cannot call (HTTP 401, 403, 404) is skipped for the rest of the session; rate limits, 5xx, timeouts and empty replies just fall through to the next model.
- Do not read or edit `.env` through the assistant's tools (a project hook blocks it); the code reads it at runtime.
- Commit with the GitHub no-reply identity: `git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit ...`; `git push` after each task.

## File Structure

| File | Responsibility |
| --- | --- |
| `.gitignore` | ignore `.athena/` (spend record) |
| `src/athena/llm/errors.py` | `ModelNotAllowed`, `SpendCapExceeded`, `ProviderError` |
| `src/athena/llm/envfile.py` | `read_setting`, `PROJECT_ROOT`, `DEFAULT_ENV_FILE` |
| `src/athena/llm/policy.py` | `ModelPolicy`, `allow_all`, `free_only`, `allowlist`, `OPENAI_CHEAP_MODELS` |
| `src/athena/llm/spend.py` | `SpendGuard`, pessimistic prices, `DEFAULT_STATE_FILE` |
| `src/athena/llm/client.py` | `OpenAICompatibleClient` |
| `src/athena/llm/router.py` | `PROVIDERS`, `TIERS`, `ROLE_TIERS`, `FallbackLLM`, `Router`, `StaticRouter`, `build_router` |
| `src/athena/llm/probe.py` | `probe_models`, `ProbeResult`, command-line `main` (`python -m athena.llm.probe`) |
| `tests/llm_fakes.py` | `FakeResponse`, `FakePost`, `SECRET` shared by LLM tests |
| `tests/test_llm_basics.py`, `test_llm_spend.py`, `test_llm_client.py`, `test_llm_router.py`, `test_llm_probe.py`, `tests/live/test_live_llm.py` | one test module per source module, plus the live check |

---

### Task 1: Errors, settings reader and model policy

**Files:**
- Modify: `.gitignore`
- Create: `src/athena/llm/__init__.py` (empty file), `src/athena/llm/errors.py`, `src/athena/llm/envfile.py`, `src/athena/llm/policy.py`
- Test: `tests/test_llm_basics.py`

**Interfaces:**
- Produces (`athena.llm.errors`): `ModelNotAllowed(AthenaError)`, `SpendCapExceeded(AthenaError)`, `ProviderError(AthenaError)` with attributes `status: int | None` and `retryable: bool`, constructed as `ProviderError(message, status=None, retryable=False)`.
- Produces (`athena.llm.envfile`): `PROJECT_ROOT: Path`; `DEFAULT_ENV_FILE: Path`; `read_setting(name, env_file=DEFAULT_ENV_FILE, environ=None) -> str` (process environment first, then the file; `""` when neither has it; `environ=None` means `os.environ`).
- Produces (`athena.llm.policy`): `OPENAI_CHEAP_MODELS: tuple[str, ...]`; `OPENROUTER_FREE_ROUTER = "openrouter/free"`; `@dataclass(frozen=True) ModelPolicy(provider, allowed=None, free_only=False)` with `allows(model) -> bool` and `check(model)` (raises `ModelNotAllowed`); constructors `allow_all(provider)`, `free_only(provider)`, `allowlist(provider, models)`.

- [ ] **Step 1: Write the failing tests `tests/test_llm_basics.py`**

```python
import pytest

from athena.llm.envfile import read_setting
from athena.llm.errors import ModelNotAllowed
from athena.llm.policy import OPENAI_CHEAP_MODELS, allow_all, allowlist, free_only


def test_environment_variable_wins_over_the_file(tmp_path):
    env_file = tmp_path / "settings.env"
    env_file.write_text("MY_KEY=from-file\n", encoding="utf-8")
    assert read_setting("MY_KEY", env_file, {"MY_KEY": "from-environ"}) == "from-environ"
    assert read_setting("MY_KEY", env_file, {}) == "from-file"


def test_env_file_parsing_handles_comments_quotes_blanks_and_equals_in_values(tmp_path):
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        '# a comment\n\nA="quoted"\nB=\'single\'\nC=plain=with=equals\n  D = spaced \nNOEQUALS\nEMPTY=\n', encoding="utf-8"
    )
    assert read_setting("A", env_file, {}) == "quoted"
    assert read_setting("B", env_file, {}) == "single"
    assert read_setting("C", env_file, {}) == "plain=with=equals"
    assert read_setting("D", env_file, {}) == "spaced"
    assert read_setting("EMPTY", env_file, {}) == ""
    assert read_setting("NOEQUALS", env_file, {}) == ""
    assert read_setting("MISSING", env_file, {}) == ""


def test_missing_env_file_gives_an_empty_value(tmp_path):
    assert read_setting("ANY", tmp_path / "nope.env", {}) == ""


def test_free_only_policy_accepts_free_ids_and_the_free_router_only():
    policy = free_only("openrouter")
    assert policy.allows("nvidia/nemotron-3-super-120b-a12b:free")
    assert policy.allows("openrouter/free")
    assert not policy.allows("nvidia/nemotron-3-super-120b-a12b")
    assert not policy.allows("openai/gpt-5.4")
    with pytest.raises(ModelNotAllowed, match="free models only"):
        policy.check("anthropic/claude-opus-5-5")


def test_allowlist_policy_rejects_anything_not_listed():
    policy = allowlist("openai", OPENAI_CHEAP_MODELS)
    assert policy.allows("gpt-5.4-nano") and policy.allows("gpt-4o-mini")
    for expensive in ("gpt-5.4", "gpt-5", "o3", "gpt-4o", "gpt-5.4-pro"):
        assert not policy.allows(expensive), expensive
    with pytest.raises(ModelNotAllowed, match="allowlist"):
        policy.check("gpt-5.4")


def test_allow_all_policy_allows_any_id():
    assert allow_all("nvidia").allows("anything/at-all")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_llm_basics.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.llm'`.

- [ ] **Step 3: Create the empty `src/athena/llm/__init__.py`, add `.athena/` to `.gitignore`, and write the three modules**

Add this line to `.gitignore` after `graphify-out/`:

```
.athena/
```

`src/athena/llm/errors.py`:

```python
from __future__ import annotations

from athena.contracts import AthenaError


class ModelNotAllowed(AthenaError):
    """A model id is outside the provider's spending policy (for example a paid OpenRouter model)."""


class SpendCapExceeded(AthenaError):
    """A request would push a provider's cumulative spend past its hard cap, or the spend record cannot be trusted."""


class ProviderError(AthenaError):
    """A provider call failed. `status` is the HTTP status (None for network errors); `retryable` says whether
    trying the same model again could help (rate limits, server errors, timeouts)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
```

`src/athena/llm/envfile.py`:

```python
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def read_setting(name: str, env_file: Path | str = DEFAULT_ENV_FILE, environ: Mapping[str, str] | None = None) -> str:
    """A setting from the process environment, else from the env file; "" if neither has a value.

    The value is returned to the caller and never logged or printed here."""
    value = (os.environ if environ is None else environ).get(name, "")
    if value:
        return value
    path = Path(env_file)
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() == name:
            return raw.strip().strip('"').strip("'")
    return ""
```

`src/athena/llm/policy.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_llm_basics.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `6 passed`, then `277 passed, 39 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add .gitignore src/athena/llm tests/test_llm_basics.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add LLM errors, settings reader and per-provider model policy"
git push
```

---

### Task 2: Persisted spend guard

**Files:**
- Create: `src/athena/llm/spend.py`
- Test: `tests/test_llm_spend.py`

**Interfaces:**
- Consumes: `PROJECT_ROOT` (Task 1), `SpendCapExceeded` (Task 1).
- Produces (`athena.llm.spend`): `DEFAULT_STATE_FILE: Path` (`<project>/.athena/llm_spend.json`); constants `PESSIMISTIC_INPUT_USD_PER_MTOK = 2.5`, `PESSIMISTIC_OUTPUT_USD_PER_MTOK = 10.0`, `CHARS_PER_TOKEN = 3`; `SpendGuard(provider: str, cap_usd: float, path=DEFAULT_STATE_FILE)` with `spent() -> float`, static `cost(input_tokens, output_tokens) -> float`, `estimate(prompt_chars, max_output_tokens) -> float`, `check(estimated_usd)` (raises `SpendCapExceeded` when `spent + estimate > cap`), `record(input_tokens, output_tokens) -> float` (adds to the persisted total, atomic write). An unreadable record raises `SpendCapExceeded` from `spent`, `check` and `record`; a negative cap raises `ValueError`.

- [ ] **Step 1: Write the failing tests `tests/test_llm_spend.py`**

```python
import pytest

from athena.llm.errors import SpendCapExceeded
from athena.llm.spend import SpendGuard


def test_cost_uses_pessimistic_flat_prices():
    assert SpendGuard.cost(1_000_000, 0) == 2.5
    assert SpendGuard.cost(0, 1_000_000) == 10.0
    assert SpendGuard.cost(1000, 2000) == pytest.approx(0.0225)


def test_estimate_overestimates_input_tokens_and_charges_the_full_output_allowance():
    guard = SpendGuard("openai", 1.0, "unused")
    assert guard.estimate(prompt_chars=300, max_output_tokens=1000) == SpendGuard.cost(100, 1000)


def test_a_fresh_guard_has_spent_nothing(tmp_path):
    assert SpendGuard("openai", 1.0, tmp_path / "spend.json").spent() == 0.0


def test_recorded_spend_persists_across_guard_instances(tmp_path):
    path = tmp_path / "state" / "spend.json"
    first = SpendGuard("openai", 1.0, path)
    first.record(1000, 2000)
    first.record(1000, 2000)
    assert SpendGuard("openai", 1.0, path).spent() == pytest.approx(0.045)


def test_providers_are_tracked_separately(tmp_path):
    path = tmp_path / "spend.json"
    SpendGuard("openai", 1.0, path).record(1_000_000, 0)
    assert SpendGuard("other", 1.0, path).spent() == 0.0


def test_check_blocks_a_request_that_would_pass_the_cap_but_allows_one_that_fits(tmp_path):
    guard = SpendGuard("openai", 0.05, tmp_path / "spend.json")
    guard.record(1000, 2000)  # $0.0225 spent
    guard.check(0.027)  # lands at 0.0495, under the cap
    with pytest.raises(SpendCapExceeded, match="would pass the cap"):
        guard.check(0.03)


def test_an_unreadable_spend_record_fails_closed(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text("{not json", encoding="utf-8")
    guard = SpendGuard("openai", 1.0, path)
    with pytest.raises(SpendCapExceeded, match="unreadable"):
        guard.check(0.0)
    with pytest.raises(SpendCapExceeded):
        guard.record(1, 1)


def test_negative_cap_is_rejected():
    with pytest.raises(ValueError):
        SpendGuard("openai", -1.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_llm_spend.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.llm.spend'`.

- [ ] **Step 3: Write `src/athena/llm/spend.py`**

```python
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from athena.llm.envfile import PROJECT_ROOT
from athena.llm.errors import SpendCapExceeded

DEFAULT_STATE_FILE = PROJECT_ROOT / ".athena" / "llm_spend.json"
# Deliberately pessimistic flat prices, far above any nano/mini model, so the cap holds even though
# real prices are not tracked here. Dollars per million tokens.
PESSIMISTIC_INPUT_USD_PER_MTOK = 2.5
PESSIMISTIC_OUTPUT_USD_PER_MTOK = 10.0
CHARS_PER_TOKEN = 3  # real text averages about 4, so this overestimates input tokens


class SpendGuard:
    """Hard cumulative spend cap for one provider, persisted across runs so a restart cannot reset it.

    Fails closed: an unreadable spend record raises instead of being treated as zero."""

    def __init__(self, provider: str, cap_usd: float, path: Path | str = DEFAULT_STATE_FILE):
        if cap_usd < 0:
            raise ValueError("cap_usd must not be negative")
        self.provider = provider
        self.cap_usd = cap_usd
        self.path = Path(path)

    def _load(self) -> dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(key): float(value) for key, value in data.items()}
        except (OSError, ValueError, AttributeError) as exc:
            raise SpendCapExceeded(f"spend record {self.path} is unreadable ({exc}); refusing to spend") from exc

    def spent(self) -> float:
        return self._load().get(self.provider, 0.0)

    @staticmethod
    def cost(input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * PESSIMISTIC_INPUT_USD_PER_MTOK + output_tokens * PESSIMISTIC_OUTPUT_USD_PER_MTOK
        ) / 1_000_000

    def estimate(self, prompt_chars: int, max_output_tokens: int) -> float:
        return self.cost(math.ceil(prompt_chars / CHARS_PER_TOKEN), max_output_tokens)

    def check(self, estimated_usd: float) -> None:
        spent = self.spent()
        if spent + estimated_usd > self.cap_usd:
            raise SpendCapExceeded(
                f"{self.provider}: spent ${spent:.4f} of ${self.cap_usd:.2f}; "
                f"a request estimated at ${estimated_usd:.4f} would pass the cap"
            )

    def record(self, input_tokens: int, output_tokens: int) -> float:
        usd = self.cost(input_tokens, output_tokens)
        state = self._load()
        state[self.provider] = round(state.get(self.provider, 0.0) + usd, 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return usd
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_llm_spend.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `8 passed`, then `285 passed, 39 skipped`.

- [ ] **Step 5: Commit and push**

```bash
git add src/athena/llm/spend.py tests/test_llm_spend.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add persisted, fail-closed spend guard"
git push
```

---

### Task 3: OpenAI-compatible client

**Files:**
- Create: `src/athena/llm/client.py`, `tests/llm_fakes.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `ProviderError` (Task 1), `ModelPolicy` (Task 1), `SpendGuard` (Task 2).
- Produces (`athena.llm.client`): `RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}`; `OpenAICompatibleClient(provider, model, base_url, api_key, policy, guard=None, token_param="max_tokens", send_temperature=True, max_tokens=3000, timeout=60.0, retries=2, post=requests.post, sleep=time.sleep)` with property `name` (`"provider:model"`), counter `calls`, and `complete(system: str, user: str) -> str`. The policy is checked at construction and before each request; the guard is checked before the first request and updated from reported usage after a success (worst case if usage is missing). Retryable failures back off `2**attempt` seconds; HTTP 401/403/404 and other non-retryable statuses raise at once. A 200 reply with no `choices` raises `ProviderError` whose `status` is the embedded error code if there is one.
- Produces (`tests/llm_fakes.py`): `SECRET`, `FakeResponse(status=200, content="hello", usage=None, text="", body=None)`, `FakePost(*responses)` recording `calls` (each with `url`, `headers`, `json`, `timeout`); an `Exception` in the response list is raised instead of returned.

- [ ] **Step 1: Create the shared fakes `tests/llm_fakes.py` and write the failing tests `tests/test_llm_client.py`**

`tests/llm_fakes.py`:

```python
SECRET = "nvapi-SECRET-VALUE"


class FakeResponse:
    def __init__(self, status=200, content="hello", usage=None, text="", body=None):
        self.status_code = status
        self._body = body if body is not None else {"choices": [{"message": {"content": content}}]}
        if usage is not None and body is None:
            self._body["usage"] = usage
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item
```

`tests/test_llm_client.py`:

```python
import pytest
import requests

from athena.llm.client import OpenAICompatibleClient
from athena.llm.errors import ModelNotAllowed, ProviderError, SpendCapExceeded
from athena.llm.policy import allow_all, allowlist, free_only
from athena.llm.spend import SpendGuard
from llm_fakes import SECRET, FakePost, FakeResponse

def make(post, policy=None, model="some/model", **options):
    sleeps = []
    client = OpenAICompatibleClient(
        "test", model, "https://example.test/v1/", SECRET, policy or allow_all("test"),
        post=post, sleep=sleeps.append, **options,
    )
    client.sleeps = sleeps
    return client


def test_success_sends_an_openai_style_request_and_returns_the_text():
    post = FakePost(FakeResponse(content="  the answer  "))
    client = make(post, max_tokens=500)
    assert client.complete("be brief", "hi") == "  the answer  "
    call = post.calls[0]
    assert call["url"] == "https://example.test/v1/chat/completions"
    assert call["headers"] == {"Authorization": f"Bearer {SECRET}"}
    assert call["json"]["model"] == "some/model"
    assert call["json"]["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert call["json"]["max_tokens"] == 500 and call["json"]["temperature"] == 0


def test_parameter_profile_can_use_max_completion_tokens_and_no_temperature():
    post = FakePost(FakeResponse())
    make(post, token_param="max_completion_tokens", send_temperature=False, max_tokens=300).complete("s", "u")
    sent = post.calls[0]["json"]
    assert sent["max_completion_tokens"] == 300 and "max_tokens" not in sent and "temperature" not in sent


def test_empty_content_from_a_reasoning_model_comes_back_as_an_empty_string():
    assert make(FakePost(FakeResponse(content=None))).complete("s", "u") == ""


def test_a_disallowed_model_is_rejected_when_the_client_is_built_before_any_request():
    post = FakePost(FakeResponse())
    with pytest.raises(ModelNotAllowed):
        make(post, policy=free_only("openrouter"), model="openai/gpt-5.4")
    with pytest.raises(ModelNotAllowed):
        make(post, policy=allowlist("openai", ["gpt-5.4-nano"]), model="gpt-5.4")
    assert post.calls == []


def test_rate_limits_and_server_errors_are_retried_with_backoff_then_succeed():
    post = FakePost(FakeResponse(429), FakeResponse(503), FakeResponse(content="finally"))
    client = make(post, retries=2)
    assert client.complete("s", "u") == "finally"
    assert len(post.calls) == 3 and client.calls == 3
    assert client.sleeps == [2.0, 4.0]


def test_retries_run_out_with_a_retryable_error():
    client = make(FakePost(FakeResponse(500, text="boom")), retries=1)
    with pytest.raises(ProviderError) as caught:
        client.complete("s", "u")
    assert caught.value.retryable and caught.value.status == 500 and client.calls == 2


def test_not_found_and_auth_errors_are_not_retried():
    for status in (401, 403, 404):
        post = FakePost(FakeResponse(status, text="nope"))
        with pytest.raises(ProviderError) as caught:
            make(post).complete("s", "u")
        assert caught.value.status == status and not caught.value.retryable and len(post.calls) == 1


def test_network_errors_and_timeouts_are_retryable():
    post = FakePost(requests.Timeout("slow"), FakeResponse(content="ok"))
    assert make(post, retries=1).complete("s", "u") == "ok"
    with pytest.raises(ProviderError) as caught:
        make(FakePost(requests.ConnectionError("down")), retries=0).complete("s", "u")
    assert caught.value.retryable and caught.value.status is None


def test_the_api_key_never_appears_in_error_messages():
    echo = FakePost(FakeResponse(400, text=f"bad request for key {SECRET}"))
    with pytest.raises(ProviderError) as caught:
        make(echo).complete("s", "u")
    assert SECRET not in str(caught.value) and "<key>" in str(caught.value)


def test_spend_is_recorded_from_reported_usage(tmp_path):
    guard = SpendGuard("test", 1.0, tmp_path / "spend.json")
    client = make(FakePost(FakeResponse(usage={"prompt_tokens": 1000, "completion_tokens": 2000})), guard=guard)
    client.complete("s", "u")
    assert guard.spent() == pytest.approx(0.0225)


def test_missing_usage_is_charged_at_the_worst_case(tmp_path):
    guard = SpendGuard("test", 10.0, tmp_path / "spend.json")
    make(FakePost(FakeResponse()), guard=guard, max_tokens=1000).complete("s" * 30, "u" * 30)
    assert guard.spent() == pytest.approx(SpendGuard.cost(20, 1000))


def test_a_request_past_the_spend_cap_is_blocked_before_it_is_sent(tmp_path):
    guard = SpendGuard("test", 0.001, tmp_path / "spend.json")
    post = FakePost(FakeResponse())
    client = make(post, guard=guard, max_tokens=3000)  # worst case $0.03 against a $0.001 cap
    with pytest.raises(SpendCapExceeded):
        client.complete("s", "u")
    assert post.calls == [] and client.calls == 0


def test_a_200_reply_carrying_a_retryable_error_body_is_retried():
    rate_limited = FakeResponse(body={"error": {"message": "Provider returned error", "code": 429}})
    client = make(FakePost(rate_limited, FakeResponse(content="ok")), retries=1)
    assert client.complete("s", "u") == "ok" and client.calls == 2


def test_a_200_reply_carrying_a_permanent_error_body_is_not_retried():
    gone = FakeResponse(body={"error": {"message": "no such model", "code": 404}})
    post = FakePost(gone)
    with pytest.raises(ProviderError) as caught:
        make(post, retries=2).complete("s", "u")
    assert caught.value.status == 404 and not caught.value.retryable and len(post.calls) == 1


def test_a_200_reply_with_no_choices_or_not_json_is_a_retryable_provider_error():
    for body in ({"unexpected": True}, {"choices": []}, ValueError("not json")):
        with pytest.raises(ProviderError) as caught:
            make(FakePost(FakeResponse(body=body)), retries=0).complete("s", "u")
        assert caught.value.retryable, body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_llm_client.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.llm.client'`.

- [ ] **Step 3: Write `src/athena/llm/client.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_llm_client.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `15 passed`, then `300 passed, 39 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `client.py`, replace the line `self.guard.check(self.guard.estimate(len(system) + len(user), self.max_tokens))` with `pass` and run `.venv/Scripts/python -m pytest tests/test_llm_client.py -q`: expect `test_a_request_past_the_spend_cap_is_blocked_before_it_is_sent` to FAIL. Undo it. Then replace the first `policy.check(model)` (in `__init__`) with `pass`: expect `test_a_disallowed_model_is_rejected_when_the_client_is_built_before_any_request` to FAIL. Undo it and confirm `git diff` is empty for `client.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/llm/client.py tests/llm_fakes.py tests/test_llm_client.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add OpenAI-compatible client with retries, policy and spend guard"
git push
```

---

### Task 4: Fallback chain, tiers and router

**Files:**
- Create: `src/athena/llm/router.py`
- Test: `tests/test_llm_router.py`

**Interfaces:**
- Consumes: `OpenAICompatibleClient`, `RETRYABLE_STATUSES` (Task 3); `ModelPolicy` constructors and `OPENAI_CHEAP_MODELS` (Task 1); `SpendGuard`, `DEFAULT_STATE_FILE` (Task 2); `read_setting`, `DEFAULT_ENV_FILE` (Task 1); `FallbackChain`, `HealthRegistry` from `athena.fallback`; `LLMClient` from `athena.agents.base`; `FakePost`, `FakeResponse` (Task 3, tests only).
- Produces (`athena.llm.router`): `DEFAULT_OPENAI_CAP_USD = 1.0`; `@dataclass(frozen=True) ProviderSpec(name, base_url, key_name, policy, token_param="max_tokens", send_temperature=True, capped=False)`; `PROVIDERS: dict[str, ProviderSpec]` (`nvidia`, `openrouter`, `openai`); `TIERS: dict[str, tuple[tuple[str, str], ...]]` (`cheap`, `mid`, `strong`, each a fallback order of `(provider, model)`); `ROLE_TIERS = {"resolver": "cheap", "specialist": "mid", "judge": "strong"}`; `DEFAULT_TIER = "mid"`; `FallbackLLM(clients, health=None)` with `clients`, `names`, `health`, `last_source`, `last_failures` and `complete(system, user) -> str` (raises `AllSourcesFailed` naming every failure); `Router` protocol with `client_for(role) -> LLMClient`; `StaticRouter(tiers, role_tiers=ROLE_TIERS)` with `client_for(role) -> FallbackLLM`; `build_router(env_file=DEFAULT_ENV_FILE, environ=None, spend_file=DEFAULT_STATE_FILE, tiers=TIERS, **client_options) -> StaticRouter` (only providers whose key is set; raises `AthenaError` if no key is set).

- [ ] **Step 1: Write the failing tests `tests/test_llm_router.py`**

```python
import pytest

from athena.contracts import AllSourcesFailed, AthenaError
from athena.llm.client import OpenAICompatibleClient
from athena.llm.policy import allow_all
from athena.llm.router import PROVIDERS, ROLE_TIERS, TIERS, FallbackLLM, StaticRouter, build_router
from llm_fakes import FakePost, FakeResponse


def client(name, post):
    return OpenAICompatibleClient("p", name, "https://x.test/v1", "k", allow_all("p"), post=post, sleep=lambda s: None, retries=0)


def test_first_working_model_answers_and_failures_are_recorded():
    llm = FallbackLLM([client("a", FakePost(FakeResponse(500))), client("b", FakePost(FakeResponse(content="from b")))])
    assert llm.complete("s", "u") == "from b"
    assert llm.last_source == "p:b"
    assert llm.last_failures and llm.last_failures[0][0] == "p:a"


def test_an_empty_reply_counts_as_a_failure():
    llm = FallbackLLM([client("a", FakePost(FakeResponse(content=None))), client("b", FakePost(FakeResponse(content="ok")))])
    assert llm.complete("s", "u") == "ok" and llm.last_source == "p:b"


def test_a_model_the_account_cannot_use_is_skipped_for_the_rest_of_the_session():
    first_post = FakePost(FakeResponse(404, text="not found"))
    llm = FallbackLLM([client("a", first_post), client("b", FakePost(FakeResponse(content="ok")))])
    llm.complete("s", "u")
    llm.complete("s", "u")
    assert len(first_post.calls) == 1  # never tried again
    assert llm.health.is_degraded("p:a")


def test_a_transient_failure_does_not_degrade_the_model():
    flaky = FakePost(FakeResponse(503), FakeResponse(content="back"))
    llm = FallbackLLM([client("a", flaky)])
    with pytest.raises(AllSourcesFailed):
        llm.complete("s", "u")
    assert not llm.health.is_degraded("p:a")
    assert llm.complete("s", "u") == "back"


def test_when_every_model_fails_the_error_names_each_one():
    llm = FallbackLLM([client("a", FakePost(FakeResponse(500))), client("b", FakePost(FakeResponse(404)))])
    with pytest.raises(AllSourcesFailed) as caught:
        llm.complete("s", "u")
    assert "p:a" in str(caught.value) and "p:b" in str(caught.value)


def test_static_router_maps_roles_to_tiers_with_a_default():
    tiers = {name: FallbackLLM([client(name, FakePost(FakeResponse()))]) for name in ("cheap", "mid", "strong")}
    router = StaticRouter(tiers)
    assert router.client_for("resolver") is tiers["cheap"]
    assert router.client_for("specialist") is tiers["mid"]
    assert router.client_for("judge") is tiers["strong"]
    assert router.client_for("something-new") is tiers["mid"]
    with pytest.raises(AthenaError):
        StaticRouter({"cheap": tiers["cheap"]}).client_for("judge")


def test_every_configured_tier_entry_obeys_its_providers_policy():
    for tier, entries in TIERS.items():
        assert entries, tier
        for provider, model in entries:
            PROVIDERS[provider].policy.check(model)  # raises ModelNotAllowed on a paid OpenRouter or non-allowlisted OpenAI id
    assert set(ROLE_TIERS.values()) <= set(TIERS)


def test_build_router_uses_only_providers_with_keys(tmp_path):
    router = build_router(env_file=tmp_path / "none.env", environ={"NVIDIA_API_KEY": "k1"}, spend_file=tmp_path / "s.json")
    strong = router.client_for("judge")
    assert isinstance(strong, FallbackLLM)
    assert strong.names == ["nvidia:nvidia/nemotron-3-ultra-550b-a55b"]


def test_build_router_with_all_keys_chains_nvidia_then_openrouter_then_openai(tmp_path):
    environ = {"NVIDIA_API_KEY": "k1", "OPENROUTER_API_KEY": "k2", "OPENAI_API_KEY": "k3"}
    router = build_router(env_file=tmp_path / "none.env", environ=environ, spend_file=tmp_path / "s.json")
    names = router.client_for("specialist").names
    assert [n.split(":")[0] for n in names] == ["nvidia", "openrouter", "openai"]


def test_build_router_without_any_key_raises(tmp_path):
    with pytest.raises(AthenaError, match="no LLM provider key"):
        build_router(env_file=tmp_path / "none.env", environ={}, spend_file=tmp_path / "s.json")


def test_openai_clients_get_the_spend_guard_and_the_gpt5_parameter_profile(tmp_path):
    environ = {"OPENAI_API_KEY": "k3", "OPENAI_SPEND_CAP_USD": "0.25"}
    router = build_router(env_file=tmp_path / "none.env", environ=environ, spend_file=tmp_path / "s.json")
    openai_client = router.client_for("specialist").clients[0]
    assert openai_client.guard.cap_usd == 0.25
    assert openai_client.token_param == "max_completion_tokens" and not openai_client.send_temperature
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_llm_router.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.llm.router'`.

- [ ] **Step 3: Write `src/athena/llm/router.py`**

```python
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
from athena.llm.policy import OPENAI_CHEAP_MODELS, ModelPolicy, allow_all, allowlist, free_only
from athena.llm.spend import DEFAULT_STATE_FILE, SpendGuard

DEFAULT_OPENAI_CAP_USD = 1.0  # of an account holding about $4


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    key_name: str
    policy: ModelPolicy
    token_param: str = "max_tokens"
    send_temperature: bool = True
    capped: bool = False


PROVIDERS: dict[str, ProviderSpec] = {
    "nvidia": ProviderSpec("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", allow_all("nvidia")),
    "openrouter": ProviderSpec(
        "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", free_only("openrouter")
    ),
    "openai": ProviderSpec(
        "openai", "https://api.openai.com/v1", "OPENAI_API_KEY", allowlist("openai", OPENAI_CHEAP_MODELS),
        token_param="max_completion_tokens", send_temperature=False, capped=True,
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
    spend_file: Path | str = DEFAULT_STATE_FILE,
    tiers: Mapping[str, Sequence[tuple[str, str]]] = TIERS,
    **client_options: object,
) -> StaticRouter:
    """Build every tier from the providers whose keys are set. Raises if no key is set at all."""
    keys = {name: read_setting(spec.key_name, env_file, environ) for name, spec in PROVIDERS.items()}
    if not any(keys.values()):
        raise AthenaError("no LLM provider key is set (NVIDIA_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY)")
    cap = float(read_setting("OPENAI_SPEND_CAP_USD", env_file, environ) or DEFAULT_OPENAI_CAP_USD)
    guards = {"openai": SpendGuard("openai", cap, spend_file)}
    built: dict[str, FallbackLLM] = {}
    for tier, entries in tiers.items():
        clients = []
        for provider, model in entries:
            spec = PROVIDERS[provider]
            if not keys[provider]:
                continue
            clients.append(
                OpenAICompatibleClient(
                    provider, model, spec.base_url, keys[provider], spec.policy,
                    guard=guards.get(provider) if spec.capped else None,
                    token_param=spec.token_param, send_temperature=spec.send_temperature,
                    **client_options,  # type: ignore[arg-type]
                )
            )
        if clients:
            built[tier] = FallbackLLM(clients)
    return StaticRouter(built)
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_llm_router.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `11 passed`, then `311 passed, 39 skipped`.

- [ ] **Step 5: Mutation check (do not commit these edits)**

In `policy.py`, replace the condition `if self.free_only and not (model.endswith(":free") or model == OPENROUTER_FREE_ROUTER):` with `if False:` and run `.venv/Scripts/python -m pytest tests/test_llm_basics.py -q`: expect `test_free_only_policy_accepts_free_ids_and_the_free_router_only` to FAIL. Undo it and confirm `git diff` is empty for `policy.py`.

- [ ] **Step 6: Commit and push**

```bash
git add src/athena/llm/router.py tests/test_llm_router.py
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add fallback LLM chain, model tiers and static router"
git push
```

---

### Task 5: Probe, live check, docs and graph refresh

**Files:**
- Create: `src/athena/llm/probe.py`, `tests/test_llm_probe.py`, `tests/live/test_live_llm.py`
- Modify: `.env.example`, `TRD.md`

**Interfaces:**
- Consumes: `OpenAICompatibleClient` (Task 3), `PROVIDERS`, `TIERS`, `DEFAULT_OPENAI_CAP_USD`, `build_router` (Task 4), `SpendGuard`, `read_setting`, `FakePost`, `FakeResponse`.
- Produces (`athena.llm.probe`): `@dataclass(frozen=True) ProbeResult(provider, model, ok, detail, seconds)`; `probe_models(env_file=DEFAULT_ENV_FILE, environ=None, spend_file=DEFAULT_STATE_FILE, post=requests.post) -> list[ProbeResult]` (one tiny request per distinct tier model whose provider key is set; every failure kind is reported, never raised); `main(argv=None) -> int` (0 all ok, 2 some failed, 1 no key set); run as `python -m athena.llm.probe [--env-file PATH]`.

- [ ] **Step 1: Write the failing tests `tests/test_llm_probe.py`**

```python
from athena.llm.probe import PROBE_USER, main, probe_models
from athena.llm.router import TIERS
from llm_fakes import FakePost, FakeResponse


def distinct_models():
    return {entry for entries in TIERS.values() for entry in entries}


def test_probe_tries_each_distinct_model_once_for_providers_with_keys(tmp_path):
    post = FakePost(FakeResponse(content="OK"))
    results = probe_models(env_file=tmp_path / "none", environ={"NVIDIA_API_KEY": "k"}, spend_file=tmp_path / "s.json", post=post)
    nvidia_models = {model for provider, model in distinct_models() if provider == "nvidia"}
    assert {r.model for r in results} == nvidia_models and len(results) == len(nvidia_models)
    assert all(r.ok and r.provider == "nvidia" for r in results)
    assert all(call["json"]["messages"][1]["content"] == PROBE_USER for call in post.calls)


def test_probe_reports_each_failure_kind_without_raising(tmp_path):
    post = FakePost(FakeResponse(404, text="gone"), FakeResponse(content=None), FakeResponse(content="OK"))
    results = probe_models(env_file=tmp_path / "none", environ={"NVIDIA_API_KEY": "k"}, spend_file=tmp_path / "s.json", post=post)
    assert [r.ok for r in results] == [False, False, True]
    assert "HTTP 404" in results[0].detail and results[1].detail == "empty reply"


def test_probe_with_no_keys_returns_nothing_and_the_cli_says_so(tmp_path, capsys):
    assert probe_models(env_file=tmp_path / "none", environ={}, spend_file=tmp_path / "s.json") == []
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    import os

    saved = {k: os.environ.pop(k) for k in ("NVIDIA_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY") if k in os.environ}
    try:
        assert main(["--env-file", str(empty_env)]) == 1
    finally:
        os.environ.update(saved)
    assert "no provider key is set" in capsys.readouterr().out


def test_probe_charges_openai_probes_to_the_spend_guard(tmp_path):
    post = FakePost(FakeResponse(content="OK", usage={"prompt_tokens": 20, "completion_tokens": 5}))
    probe_models(env_file=tmp_path / "none", environ={"OPENAI_API_KEY": "k"}, spend_file=tmp_path / "s.json", post=post)
    assert (tmp_path / "s.json").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_llm_probe.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'athena.llm.probe'`.

- [ ] **Step 3: Write `src/athena/llm/probe.py`**

```python
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
from athena.llm.router import DEFAULT_OPENAI_CAP_USD, PROVIDERS, TIERS
from athena.llm.spend import DEFAULT_STATE_FILE, SpendGuard

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
    spend_file: Path | str = DEFAULT_STATE_FILE,
    post: Callable[..., Any] = requests.post,
) -> list[ProbeResult]:
    """One tiny request per distinct (provider, model) in the tiers whose key is set. Never shows a key."""
    cap = float(read_setting("OPENAI_SPEND_CAP_USD", env_file, environ) or DEFAULT_OPENAI_CAP_USD)
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
                guard=SpendGuard(provider, cap, spend_file) if spec.capped else None,
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
```

- [ ] **Step 4: Run to verify it passes, then the full suite**

Run: `.venv/Scripts/python -m pytest tests/test_llm_probe.py -q` then `.venv/Scripts/python -m pytest -q`
Expected: `4 passed`, then `315 passed, 39 skipped`.

- [ ] **Step 5: Write the live tests `tests/live/test_live_llm.py`**

```python
import json
from collections import defaultdict
from datetime import timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.agents.base import Specialist
from athena.agents.quant_technical import QUANT_TECHNICAL
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.evaluation.schema import validate_specialist_output
from athena.llm.probe import probe_models
from athena.llm.router import build_router
from athena.technicals.packet import build_technical_packet
from athena.trading_calendar import ist_date

pytestmark = pytest.mark.live


def test_live_every_configured_provider_answers_with_at_least_one_model():
    results = probe_models()
    if not results:
        pytest.skip("no LLM provider key is set")
    by_provider = defaultdict(list)
    for r in results:
        by_provider[r.provider].append(r)
        print(f"\n{'OK  ' if r.ok else 'FAIL'} {r.provider:10s} {r.model:45s} {r.seconds:5.1f}s {r.detail}", end="")
    for provider, provider_results in by_provider.items():
        assert any(r.ok for r in provider_results), (provider, [r.detail for r in provider_results])


def test_live_quant_technical_specialist_runs_end_to_end_through_the_router():
    try:
        router = build_router()
    except AthenaError:
        pytest.skip("no LLM provider key is set")
    llm = router.client_for("specialist")
    since = ist_date(utc_now()) - timedelta(days=760)
    bars = JugaadPriceAdapter().fetch_ohlcv("SBIN", "1d", since=since)
    packet = build_technical_packet("SBIN", utc_now(), bars)
    assert packet["missing"] == [], packet["missing_reasons"]

    output = Specialist(QUANT_TECHNICAL, llm).analyze(packet)
    print(f"\nanswered by {llm.last_source}: {json.dumps(output)[:400]}")
    assert validate_specialist_output(output) == []
    assert output["data_coverage"] == "full" and output["missing"] == []
```

- [ ] **Step 6: Run the live tests**

Run: `.venv/Scripts/python -m pytest --live tests/live/test_live_llm.py -q -s`
Expected: `2 passed`, with one line per probed model and the specialist's answer printed. A model can fail on a given day (a 429 or 503 is normal on free tiers); the test only requires one working model per provider that has a key. The run uses a few cents' worth of free credits and well under a cent of OpenAI spend; the total is recorded in `.athena/llm_spend.json`. If it fails because no provider answered, run `.venv/Scripts/python -m athena.llm.probe` and read the details before changing anything. The assistant cannot read `.env`, so if keys are suspected missing, ask the user.

- [ ] **Step 7: Update `.env.example` and `TRD.md`**

1. In `.env.example`, directly under the `OPENAI_API_KEY=` line add:

```
# Hard cap in USD on cumulative OpenAI spend, tracked in .athena/llm_spend.json (optional; default 1.00)
OPENAI_SPEND_CAP_USD=1.00
```

2. In `TRD.md`, at the end of the "Provider decision" paragraph under "Model routing / cost" (§5), append: ` *Implemented in Plan 1b:* \`athena.llm\` with one OpenAI-compatible client, a \`ModelPolicy\` per provider (OpenRouter \`:free\` only, OpenAI allowlist), a persisted fail-closed \`SpendGuard\` (default cap $1.00), a \`FallbackLLM\` chain with session-long skipping of models the account cannot call, a \`StaticRouter\` (resolver to cheap, specialist to mid, judge to strong), and \`python -m athena.llm.probe\`. A decision-model router remains optional.`
3. In the revision history, add after the Phase 1a line: `- **Oct 5, 2026 (Phase 1b)** — LLM provider layer implemented with enforced spending limits (see docs/superpowers/plans/2026-10-05-phase-1b-llm-provider-layer.md).`

- [ ] **Step 8: Commit, push, refresh graph**

```bash
git add src/athena/llm/probe.py tests/test_llm_probe.py tests/live/test_live_llm.py .env.example TRD.md
git -c user.name=maahin-1 -c user.email=177068896+maahin-1@users.noreply.github.com commit -m "feat: add model probe and live LLM checks; document provider layer in TRD"
git push
.venv/Scripts/python -m graphify update .
```

If `graphify` is missing from the venv, run `.venv/Scripts/python -m pip install graphifyy` first.

---

## Self-Review (completed)

**Spec coverage (TRD §5 provider decision -> task):** OpenAI-compatible NVIDIA client (Task 3); OpenRouter free-only rule as code (Tasks 1, 4, with a test that every configured tier entry obeys its policy); OpenAI allowlist and hard persisted spend cap (Tasks 1, 2, 3, 4); raise before any request on a disallowed model or exceeded cap (Task 3 tests assert the fake `post` was never called); fall down the chain NVIDIA -> OpenRouter free -> OpenAI cheap and skip models the account cannot call (Task 4); availability probe (Task 5); static tier router behind a `Router` interface (Task 4). Not in this plan: the orchestrator (Plan 1c), quality comparison of models (needs golden sets), a decision-model router, and any provider-specific features such as prompt caching.

**Placeholder scan:** none; every code block is the file that passed the prototype run.

**Type consistency:** `ProviderError(message, status, retryable)`, `ModelPolicy.check/allows`, `SpendGuard.check/estimate/record/cost`, `OpenAICompatibleClient(...).name/complete`, `FallbackLLM.names/clients/last_source`, `build_router(...).client_for`, `TIERS`/`PROVIDERS`, `FakePost`/`FakeResponse` match across Tasks 1-5. Test totals: 271 + 6 + 8 + 15 + 11 + 4 = 315 passed; skipped 39 + 2 live = 41.
