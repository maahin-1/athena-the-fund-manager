"""Scope guard rails for anything a person can type to a model. See `athena.guard.scope`."""
from athena.guard.scope import REFUSAL, SCOPE_PROMPT, Guard, Verdict, advice_in, clean, is_injection, untrusted

__all__ = ["REFUSAL", "SCOPE_PROMPT", "Guard", "Verdict", "advice_in", "clean", "is_injection", "untrusted"]
