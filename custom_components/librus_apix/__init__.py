"""Integracja Librus Synergia dla Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change

from .api import LibrusApiClient
from .const import DOMAIN, TIMETABLE_REFRESH_TIMES
from .coordinator import LibrusDataUpdateCoordinator
from .timetable_coordinator import LibrusTimetableCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.TODO,
    Platform.BUTTON,
)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Zachowuje zgodność z kodem, który importował klienta z modułu głównego.
__all__ = ["LibrusApiClient"]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Przygotuj przestrzeń danych integracji."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Przeładuj konto po zapisaniu nowych opcji."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Uruchom jedno konto Librus Synergia."""
    client = LibrusApiClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    if not await client.async_authenticate():
        _LOGGER.warning(
            "Pierwsze logowanie konta %s nie powiodło się; integracja użyje "
            "dostępnych danych zapasowych i ponowi próbę później",
            entry.title,
        )

    main_coordinator = LibrusDataUpdateCoordinator(hass, client, entry)
    timetable_coordinator = LibrusTimetableCoordinator(hass, client, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "main_coordinator": main_coordinator,
        "timetable_coordinator": timetable_coordinator,
    }

    # Plan ma trwały cache, dlatego jest inicjalizowany niezależnie od
    # dostępności Librusa. Dane główne pozostają niedostępne do pierwszego
    # poprawnego pobrania, ale nie blokują uruchomienia pozostałych encji.
    await timetable_coordinator.async_initialize()
    await main_coordinator.async_refresh()

    @callback
    def refresh_timetable_at_fixed_time(_: Any) -> None:
        """Uruchom dodatkowe pobranie planu o bezpiecznych porach."""
        hass.async_create_task(timetable_coordinator.async_request_refresh())

    for hour, minute in TIMETABLE_REFRESH_TIMES:
        entry.async_on_unload(
            async_track_time_change(
                hass,
                refresh_timetable_at_fixed_time,
                hour=hour,
                minute=minute,
                second=0,
            )
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Wyładuj konto i jego platformy."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
