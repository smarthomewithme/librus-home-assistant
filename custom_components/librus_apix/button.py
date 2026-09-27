"""Przyciski integracji Librus APIX."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator
from .entity import librus_device_info
from .timetable_coordinator import LibrusTimetableCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Dodaj przyciski recznego odswiezania danych."""
    runtime = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            LibrusRefreshAllDataButton(
                runtime["main_coordinator"],
                runtime["timetable_coordinator"],
                config_entry,
            ),
            LibrusRefreshTimetableButton(
                runtime["timetable_coordinator"], config_entry
            ),
        ]
    )


class LibrusRefreshAllDataButton(ButtonEntity):
    """Odswiez wszystkie dane ucznia na zadanie uzytkownika."""

    _attr_has_entity_name = True
    _attr_name = "Odśwież wszystkie dane"
    _attr_icon = "mdi:refresh-circle"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        main_coordinator: LibrusDataUpdateCoordinator,
        timetable_coordinator: LibrusTimetableCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zachowaj dostęp do obu koordynatorów ucznia."""
        self._main_coordinator = main_coordinator
        self._timetable_coordinator = timetable_coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_refresh_all_data"

    @property
    def device_info(self) -> dict[str, Any]:
        """Powiaz przycisk z urzadzeniem ucznia."""
        return librus_device_info(self._config_entry)

    async def async_press(self) -> None:
        """Pobierz oceny, wiadomosci, zadania, terminarz oraz plan lekcji."""
        await self._main_coordinator.async_request_refresh()
        await self._timetable_coordinator.async_request_refresh()


class LibrusRefreshTimetableButton(
    CoordinatorEntity[LibrusTimetableCoordinator], ButtonEntity
):
    """Odswiez plan lekcji na zadanie uzytkownika."""

    _attr_has_entity_name = True
    _attr_name = "Odśwież plan lekcji"
    _attr_icon = "mdi:calendar-refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: LibrusTimetableCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj przycisk."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_refresh_timetable"

    @property
    def device_info(self) -> dict[str, Any]:
        """Powiaz przycisk z urzadzeniem ucznia."""
        return librus_device_info(self._config_entry)

    async def async_press(self) -> None:
        """Pobierz aktualny plan z Librusa."""
        await self.coordinator.async_request_refresh()
