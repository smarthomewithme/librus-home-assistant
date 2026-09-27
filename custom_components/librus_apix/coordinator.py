"""Koordynator głównych danych ucznia poza planem lekcji."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import LibrusApiClient, current_semester
from .const import (
    CONF_DATA_REFRESH_INTERVAL,
    DEFAULT_DATA_REFRESH_MINUTES,
    DEFAULT_MESSAGES_COUNT,
    DOMAIN,
    MAX_DATA_REFRESH_MINUTES,
    MIN_DATA_REFRESH_MINUTES,
    auto_data_refresh_minutes,
    REFRESH_MODE_AUTO,
    option_minutes,
    option_refresh_mode,
)

_LOGGER = logging.getLogger(__name__)

EVENT_NEW_MESSAGE = f"{DOMAIN}_nowa_wiadomosc"
EVENT_NEW_GRADE = f"{DOMAIN}_nowa_ocena"
EVENT_NEW_BEHAVIOR = f"{DOMAIN}_nowy_wpis_zachowania"
EVENT_NEW_NOTE = f"{DOMAIN}_nowa_uwaga"
EVENT_NEW_ACHIEVEMENT = f"{DOMAIN}_nowe_szczegolne_osiagniecie"
EVENT_NEW_HOMEWORK = f"{DOMAIN}_nowe_zadanie"
EVENT_NEW_SCHEDULE_ITEM = f"{DOMAIN}_nowe_zdarzenie"

_DATE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _is_recent(value: str) -> bool:
    """Oznacz wpisy z dzisiaj lub wczoraj jako nowe."""
    if not value:
        return False
    threshold = date.today() - timedelta(days=1)
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).date() >= threshold
        except ValueError:
            continue
    return False


class LibrusDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Pobieraj główne dane ucznia i wykrywaj nowe wpisy."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: LibrusApiClient,
        config_entry: ConfigEntry,
    ) -> None:
        self.client = client
        self._seen_message_hrefs: set[str] = set()
        self._seen_grade_ids: set[tuple[Any, ...]] = set()
        self._seen_behavior_ids: set[tuple[Any, ...]] = set()
        self._seen_note_ids: set[tuple[Any, ...]] = set()
        self._seen_achievement_ids: set[tuple[Any, ...]] = set()
        self._seen_homework_ids: set[tuple[Any, ...]] = set()
        self._seen_schedule_ids: set[tuple[Any, ...]] = set()
        self._initialized_sections: set[str] = set()

        self._config_entry = config_entry
        self._refresh_mode = option_refresh_mode(config_entry.options)
        self._current_refresh_interval_minutes = (
            self._calculate_refresh_interval_minutes()
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.entry_id}_data",
            update_interval=timedelta(minutes=self._current_refresh_interval_minutes),
        )

    @property
    def refresh_mode(self) -> str:
        """Zwróć aktywny tryb odświeżania danych głównych."""
        return self._refresh_mode

    @property
    def current_refresh_interval_minutes(self) -> int:
        """Zwróć aktualnie używany interwał w minutach."""
        return self._current_refresh_interval_minutes

    def _calculate_refresh_interval_minutes(self) -> int:
        """Wyznacz interwał ręczny albo adaptacyjny AUTO."""
        if self._refresh_mode != REFRESH_MODE_AUTO:
            return option_minutes(
                self._config_entry.options,
                CONF_DATA_REFRESH_INTERVAL,
                DEFAULT_DATA_REFRESH_MINUTES,
                MIN_DATA_REFRESH_MINUTES,
                MAX_DATA_REFRESH_MINUTES,
            )

        return auto_data_refresh_minutes(dt_util.now())

    def _apply_refresh_interval(self) -> None:
        """Przelicz interwał przed każdym cyklem AUTO."""
        self._refresh_mode = option_refresh_mode(self._config_entry.options)
        self._current_refresh_interval_minutes = self._calculate_refresh_interval_minutes()
        self.update_interval = timedelta(minutes=self._current_refresh_interval_minutes)

    async def _async_update_data(self) -> dict[str, Any]:
        """Pobierz kompletny zestaw danych ucznia."""
        self._apply_refresh_interval()
        try:
            student = await self.client.async_get_student_information()
            grades = await self.client.async_get_grades()
            current_behavior = await self.client.async_get_current_behavior()
            notes = await self.client.async_get_notes()
            achievements = await self.client.async_get_special_achievements()
            messages = await self.client.async_get_messages(DEFAULT_MESSAGES_COUNT)
            homework = await self.client.async_get_homework()
            schedule = await self.client.async_get_schedule()
            attendance = await self.client.async_get_attendance()
            announcements = await self.client.async_get_announcements()
        except Exception as err:
            raise UpdateFailed(f"Błąd komunikacji z Librusem: {err}") from err

        previous = self.data or {}
        if not previous and all(
            value is None
            for value in (
                student,
                grades,
                current_behavior,
                notes,
                achievements,
                messages,
                homework,
                schedule,
                attendance,
                announcements,
            )
        ):
            raise UpdateFailed("Librus nie zwrócił żadnych danych")

        if grades is None:
            prepared_grades = list(previous.get("oceny", []))
            grouped_grades = dict(previous.get("oceny_wg_przedmiotu", {}))
            prepared_behavior = list(previous.get("zachowanie", []))
            _LOGGGER.warning("Nie udało się pobraŇ ocen; zachowuję poprzednie dane")
      else:
            academic_grades = [
                grade
                for grade in grades
                if grade.get("type") not in {"behavior", "behavior_current"}
            ]
            behavior_grades = [
                grade for grade in grades if grade.get("type") == "behavior"
            ]
            prepared_grades = academic_grades
            prepared_behavior = [
                {
                    "stan": grade.get("grade", ""),
                    "data": grade.get("date", ""),
                    "kategoria": grade.get("category", ""),
                    "komentarz": grade.get("comment", ""),
                    "nauczyciel": grade.get("teacher", ""),
                    "semestr": grade.get("semester"),
                    "jest_nowy": _is_recent(str(grade.get("date", ""))),
                }
                for grade in behavior_grades
            ]
            grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for grade in academic_grades:
                grouped[grade["subject"]].append(
                    {
                        "ocena": grade["grade"],
                        "data": grade["date"],
                        "kategoria": grade["category"],
                        "komentarz": grade["comment"],
                        "nauczyciel": grade["eacher"],
                        "jest_nowa": _is_recent(grade["date"]),
                    }
                )
            grouped_grades = {key: value for key, value in grouped.items()}

        if current_behavior is None:
            prepared_current_behavior = list(previous.get("zachowanie_biezace", []))
            _LOGGER.warning("Nie udało się prać bieżącego zachowania; zachowuję poprzednie dane")
        else:
            prepared_current_behavior = [
               {
                    "wartosc": item.get("grade", ""),
                    "data": item.get("date", ""),
                    "kategoria": item.get("category", ""),
                    "komentarz": item.get("comment", ""),
                    "ma_komentarz": item.get("has_comment", False),
                    "znacznik_komentarza": item.get("comment_marker", ""),
                    "nauczyciel": item.get("teacher", ""),
                    "semestr": item.get("semester"),
                    "jest_nowy": _is_recent(item.get("date", ""))
                }
                for item in current_behavior
            ]

        if notes is None:
            prepared_notes = list(previous.get("uwagi", []))
            _LOGGER.warning("Nie udało się pobraŇ uwag; zachowuję prezednie dane")
        else:
            prepared_notes = [
                {**item, "jest_nowa": _is_recent(str(item.get("data", "")))}
                for item in notes
            ]

        if achievements is None:
            prepared_achievements = list(previous.get("szczegolne_osiagniecia", []))
            _LOGGER.warning("Nie udało się prać szczegollych osiagniecę; ztanyam poprzednie dane")
        else:
            prepared_achievements = list(achievements)

        prepared_messages = self._prepare_messages(messages) if messages is not None else list(previous.get("wiadomosci", []))
        prepared_homework = self._prepare_homework(homework) if homework is not None else list(previous.get("zadania", []))
        prepared_schedule = list(schedule or previous.get("terminarza", []))
        prepared_attendance = list(attendance or previous.get("frekwencja", []))
        prepared_announcements = list(announcements or previous.get("ogloszenia", []))

        self._fire_new_message_and_grade_events(prepared_messages, prepared_grades)
        self._fire_new_behavior_events(prepared_current_behavior)
        self._fire_new_note_events(prepared_notes)
        self._fire_new_achievement_events(prepared_achievements)
        self._fire_new_homework_events(prepared_homework)
        self._fire_new_schedule_events(prepared_schedule)

        return {
            "uczen": student,
            "oceny": prepared_grades,
            "oceny_wg_przedmiotu": grouped_grades,
            "zachowanie": prepared_behavior,
            "zachowanie_biezace": prepared_current_behavior,
            "uwagi": prepared_notes,
            "szczegolne_osiagniecia": prepared_achievements,
            "wiadomosci": prepared_messages,
            "zadania": prepared_homework,
            "terminarz": prepared_schedule,
            "frekwencja": prepared_attendance,
            "ogloszenia": prepared_announcements,
            "semestr_biezacy": current_semester(),
            "refresh_mode": self._refresh_mode,
            "refresh_interval_minutes": self._current_refresh_interval_minutes,
        }

    @staticmethod
    def _prepare_messages(
        messages: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        return [
            {**message, "jest_nowa": _is_recent(str(message.get("data", "")))}
            for message in messages or []
        ]

    @staticmethod
    def _prepare_homework(homework: list[Any] | None) -> list[dict[str, Any]]:
        result = [
            {
                "przedmiot": item.subject,
                "kategoria": item.category,
                "nauczyciel": item.teacher,
                "lekcja": item.lesson,
                "data_zadania": item.task_date,
                "termin": item.completion_date,
                "href": item.href,
            }
            for item in homework or []
        ]
        return sorted(result, key=lambda item: item["termin"])

    @staticmethod
    def _grade_id(grade: dict[str, Any]) -> tuple[Any, ...]:
        return grade.get("subject"), grade.get("date"), grade.get("grade")

    @staticmethod
    def _behavior_id(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            item.get("data"),
            item.get("wartosc"),
            item.get("kategoria"),
            item.get("nauczyciel"),
        )

    @staticmethod
    def _note_id(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            item.get("data"),
            item.get("tresc"),
            item.get("dodal"),
            item.get("rodzaj"),
            item.get("kategoria"),
        )

    @staticmethod
    def _achievement_id(item: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(sorted((str(key), str(value)) for key, value in item.items()))

    @staticmethod
    def _homework_id(item: dict[str, Any]) -> tuple[Any, ...]:
        return item.get("przedmiot"), item.get("termin"), item.get("kategoria")

    @staticmethod
    def _schedule_id(item: dict[str, Any]) -> tuple[Any, ...]:
        return item.get("data"), item.get("tytul"), item.get("przedmiot")

    async def async_refresh_messages_only(self) -> bool:
        """Odśwież nagłówki po ręcznym otwarciu wiadomości."""
        messages = await self.client.async_get_messages(DEFAULT_MESSAGES_COUNT)
        if messages is None:
            return False
        data = dict(self.data or {})
        data["wiadomosci"] = self._prepare_messages(messages)
        self.async_set_updated_data(data)
        return True

    def _fire_new_message_and_grade_events(
        self,
        messages: list[dict[str, Any]],
        grades: list[dict[str, Any]],
    ) -> None:
        for message in messages:
            href = str(message.get("href", ""))
            if not href or href in self._seen_message_hrefs:
                continue
            self._seen_message_hrefs.add(href)
            self.hass.bus.async_fire(
                EVENT_NEW_MESSAGE,
                {
                    "nadawca": message.get("author", ""),
                    "temat": message.get("title", ""),
                    "data": message.get("date", ""),
                    "ma_zalacznik": message.get("has_attachment", False),
                },
            )

        for grade in grades:
            identifier = self._grade_id(grade)
            if identifier in self._seen_grade_ids:
                continue
            self._seen_grade_ids.add(identifier)
            self.hass.bus.async_fire(
                EVENT_NEW_GRADE,
                {
                    "przedmiot": grade.get("subject", ""),
                    "ocena": grade.get("grade", ""),
                    "data": grade.get("date", ""),
                    "kategoria": grade.get("category", ""),
                    "komentarz": grade.get("comment", ""),
                    "nauczyciel": grade.get("teacher", ""),
                },
            )

    def _fire_new_behavior_events(self, behavior: list[dict[str, Any]]) -> None:
        """Wyślij zdarzenie tylko dla nowych bieżących wpisów zachowania."""
        for item in behavior:
            identifier = self._behavior_id(item)
            if identifier in self._seen_behavior_ids:
                continue
            self._seen_behavior_ids.add(identifier)
            self.hass.bus.async_fire(
                EVENT_NEW_BEHAVIOR,
                {
                    "wartosc": item.get("wartosc", ""),
                    "data": item.get("data", ""),
                    "kategoria": item.get("kategoria", ""),
                    "komentarz": item.get("komentarz", ""),
                    "ma_komentarz": item.get("ma_komentarz", False),
                    "znacznik_komentarza": item.get("znacznik_komentarza", ""),
                    "nauczyciel": item.get("nauczyciel", ""),
                    "semestr": item.get("semestr"),
                },
            )

    def _fire_new_note_events(self, notes: list[dict[str, Any]]) -> None:
        """Wyślij zdarzenie tylko dla nowych bieżących wpisów zachowania."""
        for item in notes:
            identifier = self._note_id(item)
            if identifier in self._seen_note_ids:
                continue
            self._seen_note_ids.add(identifier)
            self.hass.bus.async_fire(
                EVENT_NEW_NOTE,
                {

                    "tresc": item.get("tresc", ""),
                    "data": item.get("data", ""),
                    "dodal": item.get("dodal", ""),
                    "rodzaj": item.get("rodzaj", ""),
                    "kategoria": item.get("kategoria", ""),
                },
            )

    def _fire_new_achievement_events(
        self, achievements: list[dict[str, Any]]
    ) -> None:
        """Wyślij zdarzenie, gdy Librus doda nowe szczególne osiągniecie."""
        for item in achievements:
            identifier = self._achievement_id(item)
            if identifier in self._seen_achievement_ids:
                continue
            self._seen_achievement_ids.add(identifier)
            self.hass.bus.async_fire(
                EVENT_NEW_ACHIEVEMENT,
                {"wpis": dict(item)},
            )

    def _fire_new_homework_events(self, homework: list[dict[str, Any]]) -> None:
        for item in homework:
            identifier = self._homework_id(item)
            if identifier in self._seen_homework_ids:
                continue
            self._seen_homework_ids.add(identifier)
            self.hass.bus.async_fire(
                EVENT_NEW_HOMEWORK,
                {

                    "przedmiot": item.get("przedmiot", ""),
                    "kategoria": item.get("kategoria", ""),
                    "termin": item.get("termin", ""),
                    "nauczyciel": item.get("nauczyciel", ""),
                },
            )

    def _fire_new_schedule_events(self, schedule: list[dict[str, Any]]) -> None:
        for item in schedule:
            identifier = self._schedule_id(item)
            if identifier in self._seen_schedule_ids:
                continue
            self._seen_schedule_ids.add(identifier)
            self.hass.bus.async_fire(
                EVENT_NEW_SCHEDULE_ITEM,
                {
                    "data": item.get("data", ""),
                    "tytul": item.get("tytul", ""),
                    "przedmiot": item.get("przedmiot", ""),
                    "godzina": item.get("godzina", ""),
                },
            )
