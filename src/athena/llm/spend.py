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
