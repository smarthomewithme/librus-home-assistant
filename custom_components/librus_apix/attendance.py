"""Czyste reguły klasyfikacji wpisów frekwencji Librusa."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def attendance_symbol(entry: Mapping[str, Any]) -> str:
    return str(entry.get("symbol", "") or "").strip().casefold()


def attendance_type(entry: Mapping[str, Any]) -> str:
    return str(entry.get("typ", "") or "").strip().casefold()


def is_late(entry: Mapping[str, Any]) -> bool:
    symbol = attendance_symbol(entry)
    entry_type = attendance_type(entry)
    return symbol == "sp" or "spóź" in entry_type or "spoz" in entry_type


def is_absence(entry: Mapping[str, Any]) -> bool:
    symbol = attendance_symbol(entry)
    entry_type = attendance_type(entry)
    return (
        symbol in {"nb", "u", "zw"}
        or "nieobec" in entry_type
        or "absence" in entry_type
        or "zwoln" in entry_type
    )


def is_excused_absence(entry: Mapping[str, Any]) -> bool:
    if not is_absence(entry):
        return False
    symbol = attendance_symbol(entry)
    entry_type = attendance_type(entry)
    if "nieuspraw" in entry_type:
        return False
    return (
        symbol in {"u", "zw"}
        or "uspraw" in entry_type
        or "zwoln" in entry_type
        or "excused" in entry_type
    )


def is_unexcused_absence(entry: Mapping[str, Any]) -> bool:
    return is_absence(entry) and not is_excused_absence(entry)


def attendance_id(entry: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Stabilny lokalny identyfikator wpisu bez przechowywania dodatkowych danych."""
    return (
        attendance_symbol(entry),
        attendance_type(entry),
        str(entry.get("data", "") or "").strip(),
        str(entry.get("przedmiot", "") or "").strip(),
        str(entry.get("nauczyciel", "") or "").strip(),
        str(entry.get("godzina", "") or "").strip(),
    )
