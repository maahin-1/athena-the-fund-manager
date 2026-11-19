"""Which version of each input a study read, written into the result.

A historical study is only as good as its record of what it read. `input_manifest` states, for one study date, the dated reference inputs (rates, the premium rows, the industry tables) that were the newest on
or before the date the study read from (`read_as_of`: the study date for a strict study, today for the approximate one), each with its own date and source, and names every input that did not exist. It
carries the study policy's fingerprint (`athena.evaluation.policy`) and the input policy's label, so a result cannot be mistaken for the other kind."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from athena.evaluation.policy import INPUT_POLICY_LABELS, POLICY, STRICT_DATED
from athena.reference.store import ReferenceStore
from athena.valuation import wacc

REQUIRED = (*wacc.REQUIRED_INPUTS, "large_firm_cap_crore", "debt_maturity_years")
COUNTRIES = ("India", "United States")


def input_manifest(store: ReferenceStore, day: date, required: Sequence[str] = REQUIRED, policy: str = STRICT_DATED, today: date | None = None) -> dict[str, Any]:
    read = day if policy == STRICT_DATED else (today or day)
    inputs: dict[str, Any] = {}
    for key in required:
        found = store.input(key, read)
        inputs[key] = None if found is None else {"as_of": found.as_of.isoformat(), "source": found.source, "value": found.value}
    premiums: dict[str, Any] = {}
    for country in COUNTRIES:
        row = store.country_premium(country, read)
        premiums[country] = None if row is None else {"as_of": row[4].isoformat(), "rating": row[0], "total_erp": row[2], "crp": row[3]}
    tables = store.latest_stat_date("damodaran", "India", read)
    return {
        "policy": policy, "label": INPUT_POLICY_LABELS[policy], "study_policy": POLICY.fingerprint(), "day": day.isoformat(), "read_as_of": read.isoformat(),
        "inputs": inputs, "missing": [key for key, value in inputs.items() if value is None], "country_premium": premiums,
        "industry_tables_as_of": tables.isoformat() if tables else None,
    }
