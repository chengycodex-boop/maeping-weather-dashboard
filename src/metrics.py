"""Forecast-verification metrics for Mae Ping rainfall and temperature.

The module intentionally returns ``None`` when a denominator is undefined.
This avoids presenting a fabricated 0% or 100% score for dry/no-event samples.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Optional, Sequence


Number = Optional[float]


def _paired(actual: Sequence[Number], forecast: Sequence[Number]) -> list[tuple[float, float]]:
    if len(actual) != len(forecast):
        raise ValueError("actual and forecast must have the same length")
    pairs = [(float(a), float(f)) for a, f in zip(actual, forecast) if a is not None and f is not None]
    if not pairs:
        raise ValueError("at least one complete actual/forecast pair is required")
    return pairs


def mae(actual: Sequence[Number], forecast: Sequence[Number]) -> float:
    pairs = _paired(actual, forecast)
    return sum(abs(f - a) for a, f in pairs) / len(pairs)


def rmse(actual: Sequence[Number], forecast: Sequence[Number]) -> float:
    pairs = _paired(actual, forecast)
    return sqrt(sum((f - a) ** 2 for a, f in pairs) / len(pairs))


def mean_bias(actual: Sequence[Number], forecast: Sequence[Number]) -> float:
    pairs = _paired(actual, forecast)
    return sum(f - a for a, f in pairs) / len(pairs)


def percent_bias(actual: Sequence[Number], forecast: Sequence[Number]) -> Optional[float]:
    pairs = _paired(actual, forecast)
    denominator = sum(a for a, _ in pairs)
    if denominator == 0:
        return None
    return 100.0 * sum(f - a for a, f in pairs) / denominator


def wape(actual: Sequence[Number], forecast: Sequence[Number]) -> Optional[float]:
    """Weighted absolute percentage error; undefined when all observations are zero."""
    pairs = _paired(actual, forecast)
    denominator = sum(abs(a) for a, _ in pairs)
    if denominator == 0:
        return None
    return 100.0 * sum(abs(f - a) for a, f in pairs) / denominator


@dataclass(frozen=True)
class Contingency:
    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int

    @property
    def pod(self) -> Optional[float]:
        denominator = self.hits + self.misses
        return self.hits / denominator if denominator else None

    @property
    def far(self) -> Optional[float]:
        denominator = self.hits + self.false_alarms
        return self.false_alarms / denominator if denominator else None

    @property
    def csi(self) -> Optional[float]:
        denominator = self.hits + self.misses + self.false_alarms
        return self.hits / denominator if denominator else None


def contingency(actual: Sequence[Number], forecast: Sequence[Number], threshold: float) -> Contingency:
    pairs = _paired(actual, forecast)
    hits = misses = false_alarms = correct_negatives = 0
    for observed, predicted in pairs:
        observed_event = observed >= threshold
        predicted_event = predicted >= threshold
        if observed_event and predicted_event:
            hits += 1
        elif observed_event:
            misses += 1
        elif predicted_event:
            false_alarms += 1
        else:
            correct_negatives += 1
    return Contingency(hits, misses, false_alarms, correct_negatives)


def brier_score(probabilities: Iterable[Number], events: Iterable[Optional[bool]]) -> float:
    pairs: list[tuple[float, bool]] = []
    for probability, event in zip(probabilities, events):
        if probability is None or event is None:
            continue
        probability = float(probability)
        if not 0.0 <= probability <= 1.0:
            raise ValueError("probabilities must be between 0 and 1")
        pairs.append((probability, bool(event)))
    if not pairs:
        raise ValueError("at least one complete probability/event pair is required")
    return sum((probability - float(event)) ** 2 for probability, event in pairs) / len(pairs)


def verification_summary(
    actual: Sequence[Number],
    forecast: Sequence[Number],
    event_threshold: Optional[float] = None,
) -> dict[str, Optional[float] | int]:
    pairs = _paired(actual, forecast)
    summary: dict[str, Optional[float] | int] = {
        "n": len(pairs),
        "mae": mae(actual, forecast),
        "rmse": rmse(actual, forecast),
        "mean_bias": mean_bias(actual, forecast),
        "percent_bias": percent_bias(actual, forecast),
        "wape": wape(actual, forecast),
    }
    if event_threshold is not None:
        table = contingency(actual, forecast, event_threshold)
        summary.update(
            {
                "hits": table.hits,
                "misses": table.misses,
                "false_alarms": table.false_alarms,
                "correct_negatives": table.correct_negatives,
                "pod": table.pod,
                "far": table.far,
                "csi": table.csi,
            }
        )
    return summary
