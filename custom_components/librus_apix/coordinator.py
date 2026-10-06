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
from .attendance import attendance_id, is_unexcused_absence
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
EVENT_NEW_UNEXCUSED_ABSENCE = f"{DOMAIN}_nowa_nieusprawiedliwiona_nieobecnosc"

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
        self._seen_attendance_ids: set[tuple[str, ...]] = set()
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

        if self.client.needs_reauth:
            self._config_entry.async_start_reauth(self.hass)
            self.client.mark_reauth_requested()

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
            prepared_descriptive = list(previous.get("oceny_opisowe", []))
            prepared_behavior = list(previous.get("zachowanie", []))
            _LOGGER.warning("Nie udało się pobrać ocen; zachowuję poprzednie dane")
        else:
            # Zwykłe oceny i zachowanie klasyfikacyjne pochodzą z librus-apix.
            # Bieżąca tabela Zachowanie jest pobierana osobno, bo ma inny
            # układ HTML i kolumnę K będącą wyłącznie odsyłaczem do komentarza.
            academic_grades = [
                grade
                for grade in grades
                if grade.get("type")
                not in {"behavior", "behavior_current", "descriptive_text"}
            ]
            descriptive_grades = [
                grade
                for grade in grades
                if grade.get("type") == "descriptive_text"
            ]
            behavior_grades = [
                grade for grade in grades if grade.get("type") == "behavior"
            ]
            prepared_grades = academic_grades
            prepared_descriptive = [
                {
                    "przedmiot": grade.get("subject", ""),
                    "wartosc": grade.get("grade", ""),
                    "data": grade.get("date", ""),
                    "opis": grade.get("comment", ""),
                    "nauczyciel": grade.get("teacher", ""),
                    "semestr": grade.get("semester"),
                    "href": grade.get("href", ""),
                    "jest_nowa": _is_recent(str(grade.get("date", ""))),
                }
                for grade in descriptive_grades
            ]
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
                        "komentarz": grade.get("comment", ""),
                        "nauczyciel": grade["teacher"],
                        "semestr": grade.get("semester"),
                        "waga": grade.get("weight"),
                        "liczy_do_sredniej": grade.get("counts_to_average"),
                        "jest_nowa": _is_recent(grade["date"]),
                    }
                )
            grouped_grades = dict(grouped)

        if current_behavior is None:
            prepared_current_behavior = list(previous.get("zachowanie_biezace", []))
            raw_current_behavior = None
            _LOGGER.warning(
                "Nie udało się pobrać bieżącego zachowania; zachowuję poprzednie dane"
            )
        else:
            raw_current_behavior = current_behavior
            prepared_current_behavior = [
                {
                    # Kod (np. 3bb) zachowujemy 1:1, bez interpretowania.
                    "wartosc": str(entry.get("grade", "") or ""),
                    "data": entry.get("date", ""),
                    "kategoria": entry.get("category", ""),
                    # To jest faktyczna treść otwierana po kliknięciu K.
                    # Sam znacznik K jest szczegółem interfejsu Librusa i nie
                    # zaśmieca atrybutów Home Assistanta.
                    "komentarz": entry.get("comment", ""),
                    "nauczyciel": entry.get("teacher", ""),
                    "semestr": entry.get("semester"),
                    "jest_nowy": _is_recent(str(entry.get("date", ""))),
                }
                for entry in current_behavior
            ]

        if notes is None:
            prepared_notes = list(previous.get("uwagi", []))
            raw_notes = None
            _LOGGER.warning("Nie udało się pobrać uwag; zachowuję poprzednie dane")
        else:
            raw_notes = notes
            prepared_notes = [
                {
                    "tresc": str(entry.get("content", "") or ""),
                    "data": str(entry.get("date", "") or ""),
                    "dodal": str(entry.get("author", "") or ""),
                    "rodzaj": str(entry.get("type", "") or ""),
                    "kategoria": str(entry.get("category", "") or ""),
                    "jest_nowa": _is_recent(str(entry.get("date", "") or "")),
                }
                for entry in notes
            ]

        if achievements is None:
            prepared_achievements = list(previous.get("szczegolne_osiagniecia", []))
            raw_achievements = None
            _LOGGER.warning(
                "Nie udało się pobrać szczególnych osiągnięć; zachowuję poprzednie dane"
            )
        else:
            raw_achievements = achievements
            prepared_achievements = [dict(entry) for entry in achievements]

        prepared_messages = (
            self._prepare_messages(messages)
            if messages is not None
            else list(previous.get("wiadomosci", []))
        )
        prepared_homework = (
            self._prepare_homework(homework)
            if homework is not None
            else list(previous.get("zadania", []))
        )
        prepared_schedule = (
            schedule if schedule is not None else list(previous.get("terminarz", []))
        )
        prepared_attendance = (
            attendance
            if attendance is not None
            else list(previous.get("frekwencja", []))
        )
        prepared_announcements = (
            announcements
            if announcements is not None
            else list(previous.get("ogloszenia", []))
        )
        stale_sections = [
            name
            for name, value in (
                ("oceny", grades),
                ("zachowanie_biezace", current_behavior),
                ("uwagi", notes),
                ("szczegolne_osiagniecia", achievements),
                ("wiadomosci", messages),
                ("zadania", homework),
                ("terminarz", schedule),
                ("frekwencja", attendance),
                ("ogloszenia", announcements),
            )
            if value is None
        ]
        last_successful_update = (
            dt_util.utcnow().isoformat()
            if len(stale_sections) < 9
            else previous.get("ostatnia_poprawna_aktualizacja")
        )
        result = {
            "student_info": student or previous.get("student_info"),
            "oceny": prepared_grades,
            "oceny_wg_przedmiotu": grouped_grades,
            "oceny_opisowe": prepared_descriptive,
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
            "ostatnia_poprawna_aktualizacja": last_successful_update,
            "nieodswiezone_sekcje": stale_sections,
        }

        self._track_changes(
            raw_messages=messages,
            raw_grades=(
                [
                    grade
                    for grade in grades
                    if grade.get("type")
                    not in {"behavior", "behavior_current", "descriptive_text"}
                ]
                if grades is not None
                else None
            ),
            raw_behavior=raw_current_behavior,
            raw_notes=raw_notes,
            raw_achievements=raw_achievements,
            raw_homework=homework,
            raw_schedule=schedule,
            raw_attendance=attendance,
            messages=prepared_messages,
            grades=prepared_grades,
            behavior=prepared_current_behavior,
            notes=prepared_notes,
            achievements=prepared_achievements,
            homework=prepared_homework,
            schedule=prepared_schedule,
            attendance=prepared_attendance,
        )

        return result

    def _track_changes(
        self,
        *,
        raw_messages: list[dict[str, Any]] | None,
        raw_grades: list[dict[str, Any]] | None,
        raw_behavior: list[dict[str, Any]] | None,
        raw_notes: list[dict[str, Any]] | None,
        raw_achievements: list[dict[str, Any]] | None,
        raw_homework: list[Any] | None,
        raw_schedule: list[dict[str, Any]] | None,
        raw_attendance: list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        grades: list[dict[str, Any]],
        behavior: list[dict[str, Any]],
        notes: list[dict[str, Any]],
        achievements: list[dict[str, Any]],
        homework: list[dict[str, Any]],
        schedule: list[dict[str, Any]],
        attendance: list[dict[str, Any]],
    ) -> None:
        """Pierwszy poprawny wynik zapamiętaj, kolejne zamień na zdarzenia."""
        messages_to_fire: list[dict[str, Any]] = []
        grades_to_fire: list[dict[str, Any]] = []
        behavior_to_fire: list[dict[str, Any]] = []
        notes_to_fire: list[dict[str, Any]] = []
        achievements_to_fire: list[dict[str, Any]] = []

        if raw_messages is not None:
            if "messages" in self._initialized_sections:
                messages_to_fire = messages
            else:
                self._seen_message_hrefs.update(
                    message["href"] for message in messages if message.get("href")
                )
                self._initialized_sections.add("messages")

        if raw_grades is not None:
            if "grades" in self._initialized_sections:
                grades_to_fire = grades
            else:
                self._seen_grade_ids.update(
                    self._grade_id(grade) for grade in grades
                )
                self._initialized_sections.add("grades")

        if raw_behavior is not None:
            if "behavior_current" in self._initialized_sections:
                behavior_to_fire = behavior
            else:
                self._seen_behavior_ids.update(
                    self._behavior_id(item) for item in behavior
                )
                self._initialized_sections.add("behavior_current")

        if raw_notes is not None:
            if "notes" in self._initialized_sections:
                notes_to_fire = notes
            else:
                self._seen_note_ids.update(self._note_id(item) for item in notes)
                self._initialized_sections.add("notes")

        if raw_achievements is not None:
            if "achievements" in self._initialized_sections:
                achievements_to_fire = achievements
            else:
                self._seen_achievement_ids.update(
                    self._achievement_id(item) for item in achievements
                )
                self._initialized_sections.add("achievements")

        if messages_to_fire or grades_to_fire:
            self._fire_new_message_and_grade_events(
                messages_to_fire,
                grades_to_fire,
            )

        if behavior_to_fire:
            self._fire_new_behavior_events(behavior_to_fire)

        if notes_to_fire:
            self._fire_new_note_events(notes_to_fire)

        if achievements_to_fire:
            self._fire_new_achievement_events(achievements_to_fire)

        if raw_homework is not None:
            if "homework" in self._initialized_sections:
                self._fire_new_homework_events(homework)
            else:
                self._seen_homework_ids.update(
                    self._homework_id(item) for item in homework
                )
                self._initialized_sections.add("homework")

        if raw_schedule is not None:
            if "schedule" in self._initialized_sections:
                self._fire_new_schedule_events(schedule)
            else:
                self._seen_schedule_ids.update(
                    self._schedule_id(item) for item in schedule
                )
                self._initialized_sections.add("schedule")

        if raw_attendance is not None:
            if "attendance" in self._initialized_sections:
                self._fire_new_unexcused_absence_events(attendance)
            else:
                self._seen_attendance_ids.update(
                    attendance_id(item) for item in attendance
                )
                self._initialized_sections.add("attendance")

    @staticmethod
    def _prepare_messages(
        messages: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        return [
            {**message, "jest_nowa": _is_recent(str(message.get("date", "")))}
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
        """Wyślij zdarzenie dla każdej nowej uwagi z dedykowanej strony Uwagi."""
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
        """Wyślij zdarzenie, gdy Librus doda nowe szczególne osiągnięcie."""
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

    def _fire_new_unexcused_absence_events(
        self, attendance: list[dict[str, Any]]
    ) -> None:
        """Wyślij event tylko dla nowego nieusprawiedliwionego wpisu."""
        for item in attendance:
            identifier = attendance_id(item)
            if identifier in self._seen_attendance_ids:
                continue
            self._seen_attendance_ids.add(identifier)
            if not is_unexcused_absence(item):
                continue
            self.hass.bus.async_fire(
                EVENT_NEW_UNEXCUSED_ABSENCE,
                {
                    "data": item.get("data", ""),
                    "przedmiot": item.get("przedmiot", ""),
                    "godzina": item.get("godzina"),
                    "nauczyciel": item.get("nauczyciel", ""),
                    "symbol": item.get("symbol", ""),
                    "typ": item.get("typ", ""),
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
