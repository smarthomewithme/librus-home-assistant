"""Stałe integracji Librus Synergia."""

from datetime import datetime, timedelta
from typing import Any, Mapping

DOMAIN = "librus_apix"
INTEGRATION_NAME = "Librus Synergia — Smart Home With Me"
BLOG_URL = "https://www.smarthomewithme.com"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_REFRESH_MODE = "refresh_mode"
CONF_DATA_REFRESH_INTERVAL = "data_refresh_interval"
CONF_TIMETABLE_REFRESH_INTERVAL = "timetable_refresh_interval"

REFRESH_MODE_AUTO = "auto"
REFRESH_MODE_MANUAL = "manual"
DEFAULT_REFRESH_MODE = REFRESH_MODE_AUTO

# W trybie AUTO główne dane są odświeżane adaptacyjnie:
# - dni robocze 06:00–18:00: co 5 min
# - dni robocze 18:00–22:00: co 15 min
# - weekend 06:00–22:00: co 30 min
# - noc 22:00–06:00: co 60 min
AUTO_SCHOOL_MINUTES = 5
AUTO_EVENING_MINUTES = 15
AUTO_WEEKEND_MINUTES = 30
AUTO_NIGHT_MINUTES = 60

DEFAULT_DATA_REFRESH_MINUTES = 30
DEFAULT_TIMETABLE_REFRESH_MINUTES = 120
MIN_DATA_REFRESH_MINUTES = 5
MAX_DATA_REFRESH_MINUTES = 360
MIN_TIMETABLE_REFRESH_MINUTES = 30
MAX_TIMETABLE_REFRESH_MINUTES = 720

# Zachowane jako publiczne stałe dla zgodności z wcześniejszymi wersjami.
SCAN_INTERVAL = timedelta(minutes=DEFAULT_DATA_REFRESH_MINUTES)
TIMETABLE_SCAN_INTERVAL = timedelta(minutes=DEFAULT_TIMETABLE_REFRESH_MINUTES)

# Dodatkowe kontrole planu przed wieczornym przygotowaniem i porannym budzikiem.
TIMETABLE_REFRESH_TIMES = ((4, 0), (20, 30))

TIMETABLE_CACHE_VERSION = 1
DEFAULT_MESSAGES_COUNT = 10

SERVICE_FETCH_MESSAGE_CONTENT = "pobierz_tresc_wiadomosci"
ATTR_MESSAGE_INDEX = "indeks"
SERVICE_FETCH_SCHEDULE_CONTENT = "pobierz_tresc_terminarza"
ATTR_SCHEDULE_INDEX = "indeks"


def auto_data_refresh_minutes(now: datetime) -> int:
    """Wyznacz adaptacyjny interwał i nie przeskakuj granicy pasma."""
    weekday = now.weekday()
    hour = now.hour

    if hour < 6:
        base = AUTO_NIGHT_MINUTES
        boundary_hour = 6
    elif hour < 18 and weekday < 5:
        base = AUTO_SCHOOL_MINUTES
        boundary_hour = 18
    elif hour < 22 and weekday < 5:
        base = AUTO_EVENING_MINUTES
        boundary_hour = 22
    elif hour < 22:
        base = AUTO_WEEKEND_MINUTES
        boundary_hour = 22
    else:
        base = AUTO_NIGHT_MINUTES
        boundary_hour = None

    if boundary_hour is not None:
        boundary = now.replace(
            hour=boundary_hour, minute=0, second=0, microsecond=0
        )
        remaining_seconds = max(0.0, (boundary - now).total_seconds())
        if remaining_seconds:
            minutes_to_boundary = max(1, int((remaining_seconds + 59) // 60))
            base = min(base, minutes_to_boundary)

    return int(base)


def option_refresh_mode(options: Mapping[str, Any]) -> str:
    """Odczytaj tryb odświeżania z zachowaniem ustawień starszych wersji.

    Jeżeli użytkownik przed aktualizacją do 1.6.0 zapisał własny interwał,
    traktujemy go jako tryb ręczny. Nowe instalacje domyślnie używają AUTO.
    """
    raw = options.get(CONF_REFRESH_MODE)
    if raw in {REFRESH_MODE_AUTO, REFRESH_MODE_MANUAL}:
        return str(raw)
    if CONF_DATA_REFRESH_INTERVAL in options:
        return REFRESH_MODE_MANUAL
    return DEFAULT_REFRESH_MODE


def option_minutes(
    options: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Odczytaj i ogranicz interwał zapisany w opcjach integracji."""
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
