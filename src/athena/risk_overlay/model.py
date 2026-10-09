from __future__ import annotations

from dataclasses import dataclass, field

# The things a profile can limit, in the order a report lists them.
MEASURES = ("volatility", "drawdown", "var_95", "cvar_95", "position", "concentration", "liquidity")
LABELS = {
    "volatility": "Volatility",
    "drawdown": "Worst drawdown",
    "var_95": "1-day VaR (95%)",
    "cvar_95": "1-day CVaR (95%)",
    "position": "Position size",
    "concentration": "Concentration (HHI)",
    "liquidity": "Amount against daily traded value",
}
OK, WARN, BREACH, UNCHECKED = "ok", "warn", "breach", "unchecked"
MAX_HOLDINGS = 500


@dataclass(frozen=True)
class Limit:
    """Warn at or above `warn`, breach at or above `hard`."""

    warn: float
    hard: float


@dataclass(frozen=True)
class RiskProfile:
    """A person's limits. A measure that is not in `limits` is switched off."""

    name: str
    limits: dict[str, Limit] = field(default_factory=dict)


@dataclass(frozen=True)
class Holding:
    symbol: str
    value: float  # rupees held now


@dataclass(frozen=True)
class Overlay:
    """Everything the risk overlay needs from the person: limits, what they own, and what they might buy."""

    profile: RiskProfile
    holdings: tuple[Holding, ...] = ()
    amount: float | None = None  # rupees they are thinking of putting in


@dataclass(frozen=True)
class Finding:
    check: str  # one of MEASURES
    status: str  # OK, WARN, BREACH or UNCHECKED
    value: float | None
    warn: float
    hard: float
    message: str

    def to_dict(self) -> dict:
        return {"check": self.check, "status": self.status, "value": self.value, "warn": self.warn, "hard": self.hard, "message": self.message}


def _limits(**pairs: tuple[float, float]) -> dict[str, Limit]:
    return {name: Limit(*pair) for name, pair in pairs.items()}


# Starting points, not calibrated to any data.
PRESETS: dict[str, RiskProfile] = {
    "conservative": RiskProfile("conservative", _limits(
        volatility=(0.25, 0.35), drawdown=(0.20, 0.30), var_95=(0.020, 0.030), cvar_95=(0.030, 0.045),
        position=(0.05, 0.10), concentration=(0.15, 0.25), liquidity=(0.01, 0.03),
    )),
    "moderate": RiskProfile("moderate", _limits(
        volatility=(0.35, 0.50), drawdown=(0.30, 0.45), var_95=(0.030, 0.045), cvar_95=(0.045, 0.065),
        position=(0.10, 0.15), concentration=(0.20, 0.30), liquidity=(0.02, 0.05),
    )),
    "aggressive": RiskProfile("aggressive", _limits(
        volatility=(0.50, 0.70), drawdown=(0.45, 0.60), var_95=(0.045, 0.065), cvar_95=(0.065, 0.090),
        position=(0.15, 0.25), concentration=(0.30, 0.45), liquidity=(0.05, 0.10),
    )),
}
