"""Binarne wskazniki Librusa gotowe do automatyzacji."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator
from .entity import librus_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Dodaj lekkie wskazniki na podstawie danych juz pobranych przez koordynator."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["main_coordinator"]
    async_add_entities(
        [
            LibrusNewMessagesBinarySensor(coordinator, config_entry),
            LibrusNewGradesBinarySensor(coordinator, config_entry),
            LibrusNewBehaviorBinarySensor(coordinator, config_entry),
            LibrusNewNotesBinarySensor(coordinator, config_entry),
            LibrusNewScheduleBinarySensor(coordinator, config_entry),
            LibrusHomeworkDueTodayBinarySensor(coordinator, config_entry),
        ]
    )


class _LibrusBinarySensor(CoordinatorEntity[LibrusDataUpdateCoordinator], BinarySensorEntity):
    """Wspolna baza binarnych encji jednego ucznia."""

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> dict[str, Any]:
        student = (self.coordinator.data or {}).get("student_info")
        return librus_device_info(self._config_entry, getattr(student, "name", None))


class LibrusNewMessagesBinarySensor(_LibrusBinarySensor):
    """Informuj, gdy w Librusie czeka nieprzeczytana wiadomość."""

    _attr_name = "Nowe wiadomości"
    _attr_icon = "mdi:email-alert-outline"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_new_messages"

    @property
    def is_on(self) -> bool:
        return any(item.get("unread", False) for item in (self.coordinator.data or {}).get("wiadomosci", []))

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        unread = sum(1 for item in (self.coordinator.data or {}).get("wiadomosci", []) if item.get("unread", False))
        return {"liczba_nieprzeczytanych": unread}


class LibrusNewGradesBinarySensor(_LibrusBinarySensor):
    """Wskaznik ocen dodanych dzisiaj lub wczoraj."""

    _attr_name = "Nowe oceny"
    _attr_icon = "mdi:school-outline"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_new_grades"

    @property
    def is_on(self) -> bool:
        return any(
            grade.get("jest_nowa", False)
            for grades in (self.coordinator.data or {}).get("oceny_wg_przedmiotu", {}).values()
            for grade in grades
        )


class LibrusNewBehaviorBinarySensor(_LibrusBinarySensor):
    """Wskazuj bieżący wpis zachowania dodany dzisiaj lub wczoraj."""

    _attr_name = "Nowy wpis zachowania"
    _attr_icon = "mdi:account-alert-outline"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_new_behavior"

    @property
    def is_on(self) -> bool:
        return any(
            item.get("jest_nowy", False)
            for item in (self.coordinator.data or {}).get("zachowanie_biezace", [])
        )

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        recent = sum(
            1
            for item in (self.coordinator.data or {}).get("zachowanie_biezace", [])
            if item.get("jest_nowy", False)
        )
        return {"liczba_nowych_wpisow": recent}


class LibrusNewNotesBinarySensor(_LibrusBinarySensor):
    """Wskazuj uwagę dodaną dzisiaj lub wczoraj."""

    _attr_name = "Nowe uwagi"
    _attr_icon = "mdi:message-alert-outline"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_new_notes"

    @property
    def is_on(self) -> bool:
        return any(
            item.get("jest_nowa", False)
            for item in (self.coordinator.data or {}).get("uwagi", [])
        )

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        recent = sum(
            1
            for item in (self.coordinator.data or {}).get("uwagi", [])
            if item.get("jest_nowa", False)
        )
        return {"liczba_nowych_uwag": recent}


class LibrusNewScheduleBinarySensor(_LibrusBinarySensor):
    """Wskaznik nadchodzącego wpisu w terminarzu."""

    _attr_name = "Terminarz ma wpisy"
    _attr_icon = "mdi:calendar-alert-outline"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_upcoming_schedule"

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("terminarz", []))


class LibrusHomeworkDueTodayBinarySensor(_LibrusBinarySensor):
    """Wskaznik zadania z terminem na dzisiaj lub po terminie."""

    _attr_name = "Zadanie na dzisiaj"
    _attr_icon = "mdi:clipboard-alert-outline"

    def __init__(self, coordinator: LibrusDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_homework_due_today"

    @property
    def is_on(self) -> bool:
        from datetime import date, datetime

        for task in (self.coordinator.data or {}).get("zadania", []):
            raw = str(task.get("termin", "") or "").strip()[:10]
            for date_format in ("%Y-%m-%d", "%d.%m.%Y"):
                try:
                    if datetime.strptime(raw, date_format).date() <= date.today():
                        return True
                except ValueError:
                    continue
        return False
