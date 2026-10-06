"""Bezpieczna diagnostyka integracji Librus Synergia."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_TO_REDACT = {
    "username",
    "password",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry
) -> dict[str, Any]:
    """Zwróć dane techniczne bez ocen, wiadomości i innych danych ucznia."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    main = runtime.get("main_coordinator")
    timetable = runtime.get("timetable_coordinator")

    main_data = getattr(main, "data", None) or {}
    timetable_data = getattr(timetable, "data", None) or {}

    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            _TO_REDACT,
        ),
        "main_coordinator": {
            "last_update_success": getattr(main, "last_update_success", None),
            "last_exception": (
                str(getattr(main, "last_exception", "") or "") or None
            ),
            "refresh_mode": getattr(main, "refresh_mode", None),
            "refresh_interval_minutes": getattr(
                main, "current_refresh_interval_minutes", None
            ),
            "last_successful_update": main_data.get(
                "ostatnia_poprawna_aktualizacja"
            ),
            "stale_sections": list(main_data.get("nieodswiezone_sekcje", [])),
        },
        "timetable_coordinator": {
            "last_update_success": getattr(timetable, "last_update_success", None),
            "last_exception": (
                str(getattr(timetable, "last_exception", "") or "") or None
            ),
            "source": timetable_data.get("source", "none"),
            "fresh": timetable_data.get("fresh", False),
            "last_successful_update": timetable_data.get(
                "last_successful_update"
            ),
            "last_attempt": timetable_data.get("last_attempt"),
            "last_error": timetable_data.get("last_error"),
            "requested_week_starts": timetable_data.get(
                "requested_week_starts", []
            ),
            "week_errors": timetable_data.get("week_errors", []),
        },
    }
