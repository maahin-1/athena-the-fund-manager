"""The chance a company fails, and what its assets fetch if it does.

A going-concern cash-flow value assumes the firm survives for ever. Damodaran blends it with a failure value: value = (1 - p) x going concern + p x proceeds, where p is the
chance the firm fails over the horizon and proceeds are what the operating assets sell for in failure (`dcf.run_fcff` does the blending; this module gives it its two inputs).

p comes from the firm's synthetic rating (`ratings.py`, interest cover to rating) through the cumulative default rates by rating that his `fcffsimpleginzu.xlsx` carries on its
"Failure Rate worksheet" (Moody's and S&P history, 1 to 10 year horizons). The table is copied below; a test reads the workbook and fails if a figure drifts. Proceeds are a share
(his default 50%) of either the firm's going-concern value (his default, "V") or its book value of capital ("B"). Tied to book value they can exceed what the firm is worth alive, which
makes failure look better than survival and raises the value of a firm whose cash flows are small; tied to the going-concern value they cannot. Both are inputs of the written policy, not forecasts: the horizon, the share and the table
are shown on the page with the value they changed."""
from __future__ import annotations

from dataclasses import dataclass

HORIZON_YEARS = 10
PROCEEDS_SHARE = 0.5

# cumulative probability of default by the end of year 1 ... 10, by rating bucket
BY_RATING: dict[str, tuple[float, ...]] = {
    "AAA": (0.0, 0.0003, 0.0013, 0.0024, 0.0035, 0.0045, 0.0051, 0.0059, 0.0064, 0.007),
    "AA": (0.0002, 0.0006, 0.0012, 0.0021, 0.0031, 0.0042, 0.005, 0.0058, 0.0065, 0.0072),
    "A": (0.0005, 0.0014, 0.0023, 0.0035, 0.0047, 0.0062, 0.0079, 0.0093, 0.0108, 0.0124),
    "BBB": (0.0016, 0.0045, 0.0078, 0.0117, 0.0158, 0.0198, 0.0233, 0.0267, 0.03, 0.0332),
    "BB": (0.0061, 0.0192, 0.0348, 0.0505, 0.0652, 0.0785, 0.0901, 0.1004, 0.1097, 0.1178),
    "B": (0.0333, 0.0771, 0.1155, 0.1458, 0.1693, 0.1883, 0.2036, 0.216, 0.227, 0.2374),
    "CCC/C": (0.2708, 0.3664, 0.4141, 0.441, 0.4619, 0.4709, 0.4826, 0.4905, 0.4976, 0.5038),
}


def bucket(rating: str) -> str:
    """The row of the table a synthetic rating falls in. The rating is written as Moody's/S&P ("Ba2/BB", "B3/B-"); the S&P part decides: AAA, AA, A, BBB, BB and B each take their plus and
    minus grades, and everything below B (CCC, CC, C, D) is the last row."""
    letters = rating.split("/")[-1].strip().upper().rstrip("+-")
    if letters in ("AAA", "AA", "A", "BBB", "BB", "B"):
        return letters
    return "CCC/C"


def failure_probability(rating: str, horizon: int = HORIZON_YEARS) -> float:
    """The chance of failing within `horizon` years (1 to 10) for a firm with this synthetic rating."""
    if not 1 <= horizon <= 10:
        raise ValueError("the failure table covers horizons of 1 to 10 years")
    return BY_RATING[bucket(rating)][horizon - 1]


@dataclass(frozen=True)
class Failure:
    probability: float
    proceeds: float | None  # an amount in the same unit as the company's capital (crore); None when the proceeds are tied to the going-concern value
    bucket: str
    horizon: int
    share: float
    tie: str = "value"  # "value" or "book"

    def sentence(self) -> str:
        what = "its going-concern value" if self.tie == "value" else f"book capital, Rs {self.proceeds or 0.0:,.0f} cr"
        return (
            f"chance of failure {self.probability:.1%} over {self.horizon} years (the {self.bucket} row of the default table); if it fails the operating assets fetch "
            f"{self.share:.0%} of {what}"
        )


def failure(rating: str, invested_capital: float | None, horizon: int = HORIZON_YEARS, share: float = PROCEEDS_SHARE, tie: str = "value") -> Failure:
    """Probability and proceeds for one firm. Tied to `"book"`, a firm with no positive book capital has proceeds of nothing; tied to `"value"` the proceeds are worked out by the DCF."""
    if not 0.0 <= share <= 1.0:
        raise ValueError("the share of value recovered in failure is a fraction between 0 and 1")
    if tie not in ("value", "book"):
        raise ValueError("proceeds are tied to the going-concern value or to book capital")
    amount = None if tie == "value" else max(invested_capital or 0.0, 0.0) * share
    return Failure(failure_probability(rating, horizon), amount, bucket(rating), horizon, share, tie)
