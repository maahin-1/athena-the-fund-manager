from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from itertools import islice, permutations
from typing import Any

from athena.contracts import Coverage
from athena.evaluation.schema import validate_specialist_output


def check_abstention(
    run: Callable[[Mapping[str, Any]], Any],
    full_inputs: Mapping[str, Any],
    critical: Sequence[str],
    optional: Sequence[str] = (),
) -> list[str]:
    """Remove each declared input in turn and confirm the specialist reports it instead of bluffing.

    Removing a critical input must give coverage 'insufficient' (an abstention); removing an optional
    one must give 'partial'; either way the output must satisfy the specialist contract and name the
    removed input under `missing`. Returns a list of failures; empty means the specialist is honest."""
    failures: list[str] = []
    expectations = [(name, Coverage.INSUFFICIENT.value) for name in critical] + [
        (name, Coverage.PARTIAL.value) for name in optional
    ]
    for removed, expected in expectations:
        inputs = {key: value for key, value in full_inputs.items() if key != removed}
        output = run(inputs)
        errors = validate_specialist_output(output)
        if errors:
            failures.append(f"without {removed!r}: invalid output: {errors}")
            continue
        if output["data_coverage"] != expected:
            failures.append(f"without {removed!r}: expected coverage {expected!r}, got {output['data_coverage']!r}")
        if removed not in output["missing"]:
            failures.append(f"without {removed!r}: the missing list does not name it: {output['missing']}")
    return failures


def check_order_invariance(
    judge: Callable[[Sequence[Any]], Mapping[str, Any]],
    parts: Sequence[Any],
    max_orderings: int = 6,
) -> list[str]:
    """A judge must not rule differently when the specialists are presented in another order."""
    orderings = list(islice(permutations(parts), max_orderings))
    baseline = judge(list(orderings[0]))["verdict"]
    failures = []
    for ordering in orderings[1:]:
        verdict = judge(list(ordering))["verdict"]
        if verdict != baseline:
            failures.append(f"verdict flipped from {baseline!r} to {verdict!r} for ordering {list(ordering)}")
    return failures
