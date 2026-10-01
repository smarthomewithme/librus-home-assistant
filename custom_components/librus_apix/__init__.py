"""Integracja Librus Synergia dla Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_track_time_change

from .acknowledgements import ACK_CATEGORIES, LibrusAcknowledgements
from .api import LibrusApiClient
from .const import DOMAIN, TIMETABLE_REFRESH_TIMES
from .smart_client import SmartHomeLibrusApiClient
from .smart_coordinator import SmartHomeLibrusDataUpdateCoordinator
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

SERVICE_ACKNOWLEDGE = "potwierdz_odczytanie"
ATTR_ACK_CATEGORY = "kategoria"
ATTR_ACK_INDEX = "indeks"

# Zachowuje zgodność z kodem, który importował klienta z modułu głównego.
__all__ = ["LibrusApiClient"]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Przygotuj przestrzeń danych integracji i usługę potwierdzania."""
    hass.data.setdefault(DOMAIN, {})

    async def async_handle_acknowledge(call: ServiceCall) -> None:
        entity_id = str(call.data[ATTR_ENTITY_ID])
        category = str(call.data[ATTR_ACK_CATEGORY])
        index = call.data.get(ATTR_ACK_INDEX)

        registry_entry = er.async_get(hass).async_get(entity_id)
        if registry_entry is None or not registry_entry.config_entry_id:
            raise HomeAssistantError(
                f"Encja {entity_id} nie należy do skonfigurowanego konta Librus"
            )

        runtime = hass.data.get(DOMAIN, {}).get(registry_entry.config_entry_id)
        if not runtime:
            raise HomeAssistantError(
                f"Konto Librus dla encji {entity_id} nie jest aktualnie dostępne"
            )

        if index is None and category in {"wiadomosci", "terminarz"}:
            state = hass.states.get(entity_id)
            attr = (
                "indeks_wybranej_wiadomosci"
                if category == "wiadomosci"
                else "indeks_wybranego_zdarzenia"
            )
            selected = state.attributes.get(attr) if state else None
            if selected is not None:
                try:
                    index = int(selected)
                except (TypeError, ValueError):
                    index = None

        coordinator = runtime["main_coordinator"]
        try:
            await coordinator.async_acknowledge(category, index=index)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_ACKNOWLEDGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ACKNOWLEDGE,
            async_handle_acknowledge,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_ENTITY_ID): cv.entity_id,
                    vol.Required(ATTR_ACK_CATEGORY): vol.In(ACK_CATEGORIES),
                    vol.Optional(ATTR_ACK_INDEX): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=99)
                    ),
                }
            ),
        )

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Przeładuj konto po zapisaniu nowych opcji."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Uruchom jedno konto Librus Synergia."""
    client = SmartHomeLibrusApiClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    if not await client.async_authenticate():
        _LOGGER.warning(
            "Pierwsze logowanie konta %s nie powiodło się; integracja użyje "
            "dostępnych danych zapasowych i ponowi próbę później",
            entry.title,
        )

    acknowledgements = LibrusAcknowledgements(hass, entry.entry_id)
    await acknowledgements.async_load()

    main_coordinator = SmartHomeLibrusDataUpdateCoordinator(
        hass,
        client,
        entry,
        acknowledgements,
    )
    timetable_coordinator = LibrusTimetableCoordinator(hass, client, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "main_coordinator": main_coordinator,
        "timetable_coordinator": timetable_coordinator,
        "acknowledgements": acknowledgements,
    }

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
