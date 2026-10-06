"""Czysta matematyka ocen Librusa, bez zależności od Home Assistanta."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def grade_value(value: Any) -> float | None:
    """Zamień typową ocenę 1-6 (+/-) na liczbę; pomiń kody i punktację."""
    raw = str(value or "").strip()
    if not raw or raw[0] not in "123456":
        return None
    try:
        base = float(raw[0])
    except ValueError:
        return None
    if "+" in raw[1:]:
        base += 0.5
    elif "-" in raw[1:]:
        base -= 0.25
    return base


def average_grades(
    grades: list[Mapping[str, Any]],
    *,
    weighted: bool = False,
) -> float | None:
    """Policz średnią, szanując flagę Librusa i opcjonalne wagi."""
    total = 0.0
    denominator = 0.0

    for grade in grades:
        counts_to_average = grade.get(
            "liczy_do_sredniej", grade.get("counts_to_average")
        )
        if counts_to_average is False:
            continue

        value = grade_value(grade.get("ocena", ""))
        if value is None:
            continue

        weight = 1.0
        if weighted:
            raw_weight = grade.get("waga", grade.get("weight"))
            try:
                parsed_weight = (
                    float(raw_weight) if raw_weight not in (None, "") else 1.0
                )
            except (TypeError, ValueError):
                parsed_weight = 1.0
            if parsed_weight > 0:
                weight = parsed_weight

        total += value * weight
        denominator += weight

    return round(total / denominator, 2) if denominator else None
