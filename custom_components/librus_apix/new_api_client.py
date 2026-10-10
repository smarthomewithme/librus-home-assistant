"""Opt-in current Synergia API backend, including restricted kindergarten accounts.

Each account owns an independent librus-synergia session. Never share cookies
between student entries. Existing librus-apix accounts do not use this client.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from dataclasses import asdict
from typing import Any

from librus_synergia import (
    Librus,
    LibrusInvalidCredentialsError,
    LibrusSessionData,
    LibrusError,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import new_api_transforms as normalize
from .api import current_semester

_LOGGER = logging.getLogger(__name__)


class NewSynergiaApiClient:
    """Adapt the current typed Librus API to the existing SHWM coordinators."""

    def __init__(
        self, username: str, password: str, hass: HomeAssistant, entry_id: str
    ) -> None:
        self._username = username
        self._password = password
        self._session_store: Store[dict[str, Any]] = Store(
            hass,
            1,
            f"librus_apix.current_api_session_{entry_id}",
            private=True,
            atomic_writes=True,
        )
        self._api = self._make_api()
        self._snapshot: Any | None = None
        self._authorization_failed = False
        self._reauth_requested = False

    def _make_api(
        self, session_data: LibrusSessionData | None = None
    ) -> Librus:
        return Librus(
            self._username,
            self._password,
            session_data=session_data,
            on_session_update=self._save_session_update,
            cache_reference_data=True,
        )

    async def _save_session_update(self, session: LibrusSessionData) -> None:
        await self._session_store.async_save(asdict(session))

    async def async_restore(self) -> None:
        """Reuse the per-account device cookie across HA restarts."""
        saved = await self._session_store.async_load()
        if isinstance(saved, dict) and isinstance(saved.get("cookies"), list):
            self._api = self._make_api(LibrusSessionData(**saved))

    @property
    def needs_reauth(self) -> bool:
        return self._authorization_failed and not self._reauth_requested

    def mark_reauth_requested(self) -> None:
        self._reauth_requested = True

    async def async_authenticate(self) -> bool:
        try:
            await self._api.login()
        except LibrusInvalidCredentialsError:
            self._authorization_failed = True
            _LOGGER.warning("Synergia odrzuciła dane logowania do nowego API")
            return False
        except LibrusError as err:
            _LOGGER.warning("Nowe API Synergii jest niedostępne: %s", type(err).__name__)
            return False
        self._authorization_failed = False
        self._reauth_requested = False
        return True

    async def async_close(self) -> None:
        session = self._api.session_data
        if session is not None:
            await self._save_session_update(session)
        await self._api.close()

    async def _refresh_snapshot(self) -> Any | None:
        """One snapshot per coordinator update, not 10 repeated downloads."""
        self._snapshot = None
        try:
            self._snapshot = await self._api.fetch_all()
            self._authorization_failed = False
        except LibrusInvalidCredentialsError:
            self._authorization_failed = True
            _LOGGER.warning("Nowe API Synergii odrzuciło dane logowania")
        except LibrusError as err:
            _LOGGER.warning(
                "Nie udało się pobrać danych z nowego API Synergii: %s",
                type(err).__name__,
            )
        return self._snapshot

    def _data(self, *sections: str) -> Any | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        failed = set(getattr(snapshot, "failed_sections", set()))
        if any(section in failed for section in sections):
            return None  # Preserve previous valid HA data on partial failure.
        return snapshot

    async def async_get_student_information(self) -> Any | None:
        snapshot = await self._refresh_snapshot()
        if snapshot is None:
            return None
        return normalize.student_info(snapshot)

    async def async_get_grades(self) -> list[dict[str, Any]] | None:
        snapshot = self._data("grades", "grade_categories")
        if snapshot is None:
            return None
        semester = current_semester()
        return [
            grade for grade in normalize.grades(snapshot)
            if grade.get("semester") in (None, semester)
        ]

    async def async_get_current_behavior(self) -> list[dict[str, Any]] | None:
        # Separate current-behaviour HTML grid is not exposed by this API.
        # Never invent grades or duplicate dedicated notes.
        return []

    async def async_get_notes(self) -> list[dict[str, str]] | None:
        snapshot = self._data("notes")
        return normalize.notes(snapshot) if snapshot else None

    async def async_get_special_achievements(self) -> list[dict[str, str]]:
        # The current API has no corresponding verified achievements endpoint.
        return []

    async def async_get_messages(
        self, count: int = 10
    ) -> list[dict[str, Any]] | None:
        snapshot = self._data("messages")
        return normalize.messages(snapshot, count=count) if snapshot else None

    async def async_get_message_content(self, href: str) -> dict[str, str] | None:
        if not href:
            return None
        try:
            message = await self._api.message(str(href), "inbox")
        except LibrusError:
            _LOGGER.warning("Nie udało się otworzyć wiadomości w nowym API")
            return None
        if message is None:
            return None
        return {
            "author": str(message.sender_name or ""),
            "title": str(message.topic or ""),
            "content": str(message.content or ""),
            "date": str(message.send_date or ""),
        }

    async def async_get_homework(self) -> list[Any] | None:
        snapshot = self._data("homework_assignments")
        return normalize.homework(snapshot) if snapshot else None

    async def async_get_schedule(self) -> list[dict[str, Any]] | None:
        snapshot = self._data("homeworks")
        return normalize.agenda(snapshot) if snapshot else None

    async def async_get_schedule_content(self, href: str) -> dict[str, str] | None:
        snapshot = self._data("homeworks")
        if snapshot is None or not str(href).startswith("new/"):
            return None
        event_id = str(href).split("/", 1)[1]
        for item in snapshot.homeworks:
            if str(item.id) == event_id:
                return {"Treść": str(item.content or "")}
        return None

    async def async_get_attendance(self) -> list[dict[str, Any]] | None:
        snapshot = self._data("attendances", "attendance_types")
        return normalize.attendance(snapshot) if snapshot else None

    async def async_get_announcements(self) -> list[dict[str, str]] | None:
        snapshot = self._data("school_notices")
        return normalize.announcements(snapshot) if snapshot else None

    async def async_get_timetable(
        self, monday_dates: Iterable[date]
    ) -> dict[str, Any] | None:
        weeks: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for monday in monday_dates:
            try:
                # This high-level call includes the kindergarten 403 fallback.
                week = await self._api.timetable(week_of=monday)
                subjects = await self._api.subjects()
                teachers = await self._api.teachers()
                classrooms = await self._api.classrooms()
                weeks.append(
                    normalize.timetable_week(
                        monday, week, subjects, teachers, classrooms
                    )
                )
            except LibrusInvalidCredentialsError:
                self._authorization_failed = True
                errors.append(
                    {"week_start": monday.isoformat(), "error_type": "invalid_auth"}
                )
            except LibrusError as err:
                errors.append(
                    {
                        "week_start": monday.isoformat(),
                        "error_type": type(err).__name__,
                    }
                )
        if not weeks:
            return None
        return {"weeks": weeks, "week_errors": errors}
