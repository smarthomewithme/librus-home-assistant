"""Koordynator planu lekcji z trwalym cache."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_TIMETABLE_REFRESH_INTERVAL,
    DEFAULT_TIMETABLE_REFRESH_MINUTES,
    DOMAIN,
    MAX_TIMETABLE_REFRESH_MINUTES,
    MIN_TIMETABLE_REFRESH_MINUTES,
    TIMETABLE_CACHE_VERSION,
    option_minutes,
)
from .timetable import normalize_timetable, weeks_to_fetch

_LOGGER = logging.getLogger(__name__)


class LibrusTimetableCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Pobieraj plan lekcji i zachowuj ostatnia poprawna kopie."""

    def __init__(
        self, hass: HomeAssistant, client: Any, config_entry: ConfigEntry
    ) -> None:
        """Zainicjalizuj koordynator."""
        interval = option_minutes(
            config_entry.options,
            CONF_TIMETABLE_REFRESH_INTERVAL,
            DEFAULT_TIMETABLE_REFRESH_MINUTES,
            MIN_TIMETABLE_REFRESH_MINUTES,
            MAX_TIMETABLE_REFRESH_MINUTES,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.entry_id}_timetable",
            update_interval=timedelta(minutes=interval),
        )
        self.client = client
        self._cache: dict[str, Any] = self._empty_cache()
        self._store: Store[dict[str, Any]] = Store(
            hass,
            TIMETABLE_CACHE_VERSION,
            f"{DOMAIN}.timetable_{config_entry.entry_id}",
            private=True,
            atomic_writes=True,
        )

    @staticmethod
    def _empty_cache() -> dict[str, Any]:
        """Zbuduj pusty, ale poprawny format danych."""
        return {
            "schema_version": TIMETABLE_CACHE_VERSION,
            "week_starts": [],
            "requested_week_starts": [],
            "lessons": [],
            "week_errors": [],
            "last_successful_update": None,
        }

    def _runtime_data(
        self,
        *,
        fresh: bool,
        source: str,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        """Dodaj do cache informacje o biezacej probie odswiezenia."""
        return {
            **self._cache,
            "fresh": fresh,
            "source": source,
            "last_attempt": dt_util.utcnow().isoformat(),
            "last_error": last_error,
        }

    async def async_initialize(self) -> None:
        """Wczytaj cache przed pierwsza proba polaczenia z Librusem."""
        cached = await self._store.async_load()
        if isinstance(cached, dict) and isinstance(cached.get("lessons"), list):
            self._cache = {
                "schema_version": TIMETABLE_CACHE_VERSION,
                "week_starts": list(cached.get("week_starts", [])),
                "requested_week_starts": list(
                    cached.get("requested_week_starts", cached.get("week_starts", []))
                ),
                "lessons": list(cached.get("lessons", [])),
                "week_errors": list(cached.get("week_errors", [])),
                "last_successful_update": cached.get("last_successful_update"),
            }
            self.async_set_updated_data(self._runtime_data(fresh=False, source="cache"))
        else:
            self.async_set_updated_data(self._runtime_data(fresh=False, source="none"))

        # Blad Librusa nie blokuje startu integracji. _async_update_data zwroci
        # ostatnia dobra kopie albo pusty, jawnie oznaczony zestaw danych.
        await self.async_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Pobierz dwa tygodnie planu, a przy bledzie zachowaj cache."""
        today = dt_util.now().date()
        mondays = weeks_to_fetch(today)

        try:
            response = await self.client.async_get_timetable(mondays)
            if response is None:
                raise RuntimeError("Librus nie zwrocil planu lekcji")

            # Zgodnosc z klientem v1.2.0 i mockami w testach: stara postac byla
            # sama lista tygodni, nowa dolacza diagnostyke nieudanych tygodni.
            if isinstance(response, dict):
                raw_weeks = response.get("weeks", [])
                week_errors = response.get("week_errors", [])
            else:
                raw_weeks = response
                week_errors = []

            if not isinstance(raw_weeks, list) or not raw_weeks:
                raise RuntimeError("Librus nie zwrocil zadnego tygodnia planu lekcji")

            lessons = normalize_timetable(raw_weeks)
            failed_week_starts = {
                str(item.get("week_start", ""))
                for item in week_errors
                if isinstance(item, dict) and item.get("week_start")
            }
            if failed_week_starts:
                # Pojedynczy błąd tygodnia nie może skasować jego ostatniej
                # poprawnej kopii, gdy drugi tydzień został pobrany prawidłowo.
                lessons.extend(
                    lesson
                    for lesson in self._cache.get("lessons", [])
                    if lesson.get("week_start") in failed_week_starts
                )
                lessons = list(
                    {
                        lesson["lesson_key"]: lesson
                        for lesson in lessons
                        if lesson.get("lesson_key")
                    }.values()
                )
                lessons.sort(
                    key=lambda item: (
                        item.get("date", ""),
                        item.get("start", ""),
                        item.get("number", 0),
                    )
                )

            fetched_week_starts = [
                str(week.get("week_start", ""))
                for week in raw_weeks
                if isinstance(week, dict) and week.get("week_start")
            ]
            available_week_starts = list(
                dict.fromkeys(
                    [
                        *fetched_week_starts,
                        *(
                            start
                            for start in self._cache.get("week_starts", [])
                            if start in failed_week_starts
                        ),
                    ]
                )
            )
            self._cache = {
                "schema_version": TIMETABLE_CACHE_VERSION,
                "week_starts": available_week_starts,
                "requested_week_starts": [monday.isoformat() for monday in mondays],
                "lessons": lessons,
                "week_errors": list(week_errors),
                "last_successful_update": dt_util.utcnow().isoformat(),
            }
            await self._store.async_save(self._cache)
            partial_error = None
            if week_errors:
                partial_error = "Nie udało się odświeżyć części tygodni planu"
            return self._runtime_data(
                fresh=not week_errors,
                source="librus",
                last_error=partial_error,
            )
        except Exception as err:  # noqa: BLE001 - cache jest celowym fail-safe
            source = "cache" if self._cache.get("last_successful_update") else "none"
            _LOGGER.warning(
                "Nie udalo sie odswiezyc planu lekcji; uzywam danych %s: %s",
                source,
                err,
            )
            return self._runtime_data(
                fresh=False,
                source=source,
                last_error=str(err),
            )
