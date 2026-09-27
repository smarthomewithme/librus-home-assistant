"""Lokalny kalendarz planu lekcji Librus."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LibrusDataUpdateCoordinator
from .entity import librus_device_info
from .timetable import active_lessons
from .timetable_coordinator import LibrusTimetableCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Dodaj kalendarze planu lekcji i terminarza."""
    runtime = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            LibrusTimetableCalendar(
                runtime["timetable_coordinator"], config_entry, hass
            ),
            LibrusScheduleCalendar(runtime["main_coordinator"], config_entry),
        ]
    )


class LibrusTimetableCalendar(
    CoordinatorEntity[LibrusTimetableCoordinator], CalendarEntity
):
    """Kalendarz zawierajacy tylko aktywne lekcje."""

    _attr_has_entity_name = True
    _attr_name = "Plan lekcji"
    _attr_icon = "mdi:calendar-school"

    def __init__(
        self,
        coordinator: LibrusTimetableCoordinator,
        config_entry: ConfigEntry,
        hass: HomeAssistant,
    ) -> None:
        """Zainicjalizuj kalendarz."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_timetable_calendar"
        self._timezone = (
            dt_util.get_time_zone(hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        )

    @property
    def device_info(self) -> dict[str, Any]:
        """Powiaz kalendarz z urzadzeniem ucznia."""
        return librus_device_info(self._config_entry)

    def _event_from_lesson(self, lesson: dict[str, Any]) -> CalendarEvent | None:
        """Zbuduj zdarzenie HA z lekcji."""
        try:
            lesson_date = date.fromisoformat(str(lesson["date"]))
            start = datetime.combine(
                lesson_date, time.fromisoformat(str(lesson["start"]))
            ).replace(tzinfo=self._timezone)
            end = datetime.combine(
                lesson_date, time.fromisoformat(str(lesson["end"]))
            ).replace(tzinfo=self._timezone)
        except (KeyError, TypeError, ValueError):
            return None
        if end <= start:
            return None

        details = [f"Lekcja {lesson.get('number', '?')}"]
        if teacher := lesson.get("teacher"):
            details.append(f"Nauczyciel: {teacher}")
        if room := lesson.get("room"):
            details.append(f"Sala: {room}")
        if lesson.get("changed"):
            details.append("Zmiana/zastepstwo z Librusa")

        return CalendarEvent(
            start=start,
            end=end,
            summary=str(lesson.get("subject", "Lekcja")),
            description="\n".join(details),
            location=str(lesson.get("room", "")) or None,
            uid=str(lesson.get("lesson_key", "")) or None,
        )

    def _events(self) -> list[CalendarEvent]:
        """Zbuduj wszystkie poprawne, aktywne zdarzenia z cache."""
        result: list[CalendarEvent] = []
        for lesson in active_lessons((self.coordinator.data or {}).get("lessons", [])):
            if event := self._event_from_lesson(lesson):
                result.append(event)
        return sorted(result, key=lambda event: event.start)

    @property
    def event(self) -> CalendarEvent | None:
        """Zwroc najblizsza lekcje, ktora jeszcze sie nie skonczyla."""
        now = dt_util.now()
        return next(
            (event for event in self._events() if event.end_datetime_local > now),
            None,
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Zwroc aktywne lekcje nachodzace na wskazany zakres."""
        return [
            event
            for event in self._events()
            if event.end_datetime_local > start_date
            and event.start_datetime_local < end_date
        ]


class LibrusScheduleCalendar(
    CoordinatorEntity[LibrusDataUpdateCoordinator], CalendarEntity
):
    """Kalendarz tylko do odczytu z wydarzeniami terminarza Librusa."""

    _attr_has_entity_name = True
    _attr_name = "Terminarz"
    _attr_icon = "mdi:calendar-alert"

    def __init__(
        self,
        coordinator: LibrusDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Zainicjalizuj kalendarz terminarza."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_schedule_calendar"

    @property
    def device_info(self) -> dict[str, Any]:
        """Powiąż kalendarz z urządzeniem ucznia."""
        return librus_device_info(self._config_entry)

    @staticmethod
    def _description(item: dict[str, Any]) -> str | None:
        """Zamień szczegóły API na czytelny opis bez zmiany ich pisowni."""
        details = item.get("szczegoly")
        lines: list[str] = []
        if isinstance(details, dict):
            lines.extend(
                f"{key}: {value}"
                for key, value in details.items()
                if str(value or "").strip()
            )
        elif str(details or "").strip():
            lines.append(str(details).strip())

        if hour := str(item.get("godzina", "") or "").strip():
            lines.append(f"Godzina: {hour}")
        if lesson_number := item.get("numer_lekcji"):
            lines.append(f"Numer lekcji: {lesson_number}")
        return "\n".join(lines) or None

    @classmethod
    def _event_from_item(cls, item: dict[str, Any]) -> CalendarEvent | None:
        """Zbuduj całodniowe zdarzenie HA z wpisu terminarza."""
        try:
            start = date.fromisoformat(str(item["data"]))
        except (KeyError, TypeError, ValueError):
            return None

        subject = str(item.get("przedmiot", "") or "").strip()
        title = str(item.get("tytul", "") or "Wydarzenie").strip()
        summary = f"[{subject}] {title}" if subject else title
        uid = str(item.get("href", "") or "").strip() or "|".join(
            str(item.get(field, "") or "").strip()
            for field in ("data", "godzina", "tytul", "przedmiot")
        )
        return CalendarEvent(
            start=start,
            end=start + timedelta(days=1),
            summary=summary,
            description=cls._description(item),
            uid=uid or None,
        )

    def _events(self) -> list[CalendarEvent]:
        """Zbuduj i uporządkuj wydarzenia zapisane w koordynatorze."""
        events = [
            event
            for item in (self.coordinator.data or {}).get("terminarz", [])
            if (event := self._event_from_item(item)) is not None
        ]
        return sorted(events, key=lambda event: event.start)

    @property
    def event(self) -> CalendarEvent | None:
        """Zwróć dzisiejsze albo najbliższe przyszłe wydarzenie."""
        today = dt_util.now().date()
        return next((event for event in self._events() if event.end > today), None)

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Zwróć wydarzenia nachodzące na wskazany zakres dat."""
        start = start_date.date()
        end = end_date.date()
        return [
            event
            for event in self._events()
            if event.end > start and event.start < end
        ]
