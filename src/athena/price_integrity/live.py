from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import lru_cache

from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent, yahoo_split_events
from athena.price_integrity.nse_actions import NseActions


def live_price_checks() -> tuple[Callable[[str], Sequence[PriceEvent]], Callable[[str], Sequence[CorporateAction]]]:
    """The two real sources of corporate events, each cached per symbol for the session (events change rarely): Yahoo's
    split records and NSE's own corporate-action rows."""
    return lru_cache(maxsize=512)(yahoo_split_events), lru_cache(maxsize=512)(NseActions())
