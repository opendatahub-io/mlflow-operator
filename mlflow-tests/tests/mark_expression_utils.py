"""Helpers for reasoning about pytest mark expressions."""

from __future__ import annotations

from itertools import product
import re

from _pytest.mark.expression import Expression


UPGRADE_PHASES = ("pre_upgrade", "post_upgrade")
_IDENTIFIER_RE = re.compile(r"\b[a-zA-Z_]\w*\b")
_PYTHON_KEYWORDS = {"and", "or", "not", "True", "False"}


class InvalidUpgradePhaseSelection(ValueError):
    """Raised when a mark expression selects multiple upgrade phases."""


def _extract_marker_names(mark_expression: str) -> list[str]:
    return sorted(set(_IDENTIFIER_RE.findall(mark_expression)) - _PYTHON_KEYWORDS)


def infer_requested_upgrade_phase(mark_expression: str | None) -> str:
    """Infer whether a mark expression requires exactly one upgrade phase."""
    if not mark_expression:
        return ""

    compiled = Expression.compile(mark_expression)
    marker_names = _extract_marker_names(mark_expression)

    satisfying_assignments: list[dict[str, bool]] = []
    for values in product([False, True], repeat=len(marker_names)):
        assignment = dict(zip(marker_names, values))
        if compiled.evaluate(lambda name, **kwargs: assignment.get(name, False)):
            satisfying_assignments.append(assignment)

    if not satisfying_assignments:
        return ""

    required_phases = [
        phase
        for phase in UPGRADE_PHASES
        if all(assignment.get(phase, False) for assignment in satisfying_assignments)
    ]
    if len(required_phases) == 1:
        return required_phases[0]
    if len(required_phases) > 1:
        raise InvalidUpgradePhaseSelection(
            "Explicit marker selection must target only one upgrade phase at a time: "
            "pre_upgrade or post_upgrade."
        )

    possible_phases = {
        phase
        for phase in UPGRADE_PHASES
        if any(assignment.get(phase, False) for assignment in satisfying_assignments)
    }
    possible_without_upgrade = any(
        not any(assignment.get(phase, False) for phase in UPGRADE_PHASES)
        for assignment in satisfying_assignments
    )

    if len(possible_phases) > 1 and not possible_without_upgrade:
        raise InvalidUpgradePhaseSelection(
            "Explicit marker selection must target only one upgrade phase at a time: "
            "pre_upgrade or post_upgrade."
        )

    return ""
