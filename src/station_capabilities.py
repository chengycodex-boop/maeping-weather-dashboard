"""Reviewed variable capabilities for operational support stations."""

from __future__ import annotations


def verification_variables(code: str, operational_decision: str) -> tuple[str, ...]:
    """Variables that can be compared at an equivalent observation grain."""
    if code == "48377":
        # ThaiWater provides daily rainfall but not the hourly accumulation
        # required to compare with the hourly baseline forecast.
        return ("temperature",)
    if operational_decision.startswith("priority_1"):
        return ("precipitation", "temperature")
    if operational_decision.startswith("priority_2"):
        return ("precipitation",)
    return ()
