"""Lista zadań Librusa tylko do odczytu dla platformy To-do HA."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
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
    """Dodaj natywną, niemodyfikowalną listę zadań domowych."""
    runtime = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [LibrusHomeworkTodoList(runtime["main_coordinator"], config_entry)]
    )


class LibrusHomeworkTodoList(
    CoordinatorEntity[LibrusDataUpdateCoordinator], TodoListEntity
):
    """Zadania domowe z Librusa jako natywna lista To-do."""

    _attr_has_entity_name = True
    _attr_name = "Zadania domowe"
    _attr_icon = "mdi:clipboard-list"
    _attr_supported_features = 0

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj listę zadań."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_homework_todo"

    @property
    def device_info(self) -> dict[str, Any]:
        """Powiąż listę z urządzeniem ucznia."""
        student = (self.coordinator.data or {}).get("student_info")
        return librus_device_info(
            self._config_entry, getattr(student, "name", None)
        )

    @staticmethod
    def _due_date(value: Any) -> date | None:
        """Rozpoznaj format terminu spotykany w librus-apix."""
        raw = str(value or "").strip()
        for candidate, date_format in (
            (raw[:10], "%Y-%m-%d"),
            (raw[:10], "%d.%m.%Y"),
        ):
            try:
                return datetime.strptime(candidate, date_format).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _uid(item: dict[str, Any]) -> str:
        """Utwórz stabilny identyfikator bez zależności od pozycji na liście."""
        href = str(item.get("href", "") or "").strip()
        if href:
            return hashlib.sha256(href.encode("utf-8")).hexdigest()[:32]
        identity = "|".join(
            str(item.get(field, "") or "").strip()
            for field in (
                "lekcja",
                "przedmiot",
                "termin",
                "kategoria",
                "nauczyciel",
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @property
    def todo_items(self) -> list[TodoItem]:
        """Zwróć zadania z pamięci koordynatora, bez wywołań sieciowych."""
        result: list[TodoItem] = []
        for item in (self.coordinator.data or {}).get("zadania", []):
            subject = str(item.get("lekcja", "") or "").strip()
            task = str(
                item.get("przedmiot", "")
                or item.get("kategoria", "")
                or "Zadanie"
            ).strip()
            summary = f"[{subject}] {task}" if subject else task
            description = "\n".join(
                line
                for line in (
                    (
                        f"Kategoria: {item.get('kategoria')}"
                        if item.get("kategoria")
                        else ""
                    ),
                    (
                        f"Nauczyciel: {item.get('nauczyciel')}"
                        if item.get("nauczyciel")
                        else ""
                    ),
                    (
                        f"Dodano: {item.get('data_zadania')}"
                        if item.get("data_zadania")
                        else ""
                    ),
                )
                if line
            )
            result.append(
                TodoItem(
                    uid=self._uid(item),
                    summary=summary,
                    status=TodoItemStatus.NEEDS_ACTION,
                    due=self._due_date(item.get("termin")),
                    description=description or None,
                )
            )
        return result
