"""Wspólne elementy encji integracji Librus Synergia."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import BLOG_URL, DOMAIN


def librus_device_info(
    config_entry: ConfigEntry,
    student_name: str | None = None,
) -> dict[str, Any]:
    """Zwróć stabilne informacje urządzenia jednego ucznia."""
    return {
        "identifiers": {(DOMAIN, config_entry.entry_id)},
        "name": f"Librus - {student_name}" if student_name else config_entry.title,
        "manufacturer": "Smart Home With Me",
        "model": "Librus Synergia (integracja nieoficjalna)",
        "configuration_url": BLOG_URL,
    }
