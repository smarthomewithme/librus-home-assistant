"""Czysta logika normalizacji planu lekcji Librus.

Modul nie zalezy od Home Assistanta. Dzieki temu parser i reguly wyboru
pierwszej lekcji mozna testowac bez uruchamiania calej instancji HA.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

WEEKDAYS_PL = (
    "poniedzialek",
    "wtorek",
    "sroda",
    "czwartek",
    "piatek",
    "sobota",
    "niedziela",
)

_CANCELLED_MARKERS = (
    "odwol",
    "anulow",
    "nie odbedzie",
    "lekcja usunieta",
    "zajecia usuniete",
)

_CHANGED_MARKERS = (
    "zastep",
    "zmian",
    "przenies",
)


def monday_for(value: date) -> date:
    """Zwroc poniedzialek tygodnia zawierajacego date."""
    return value - timedelta(days=value.weekday())


def weeks_to_fetch(value: date) -> list[date]:
    """Zwroc poczatki biezacego i nastepnego tygodnia."""
    current = monday_for(value)
    return [current, current + timedelta(days=7)]


def _get(period: Any, key: str, default: Any = "") -> Any:
    """Odczytaj pole z obiektu dataclass albo slownika."""
    if isinstance(period, Mapping):
        return period.get(key, default)
    return getattr(period, key, default)


def _json_safe(value: Any) -> Any:
    """Zamien wartosc na typ bezpieczny dla atrybutow i Store HA."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _plain_text(value: Any) -> str:
    """Splaszcz strukture i usun polskie znaki do porownan statusu."""
    if isinstance(value, Mapping):
        text = " ".join(
            f"{_plain_text(key)} {_plain_text(item)}" for key, item in value.items()
        )
    elif isinstance(value, (list, tuple, set)):
        text = " ".join(_plain_text(item) for item in value)
    else:
        text = str(value or "")
    # NFKD rozklada większość polskich znaków, ale nie literę ł/Ł.
    # Bez tej zamiany np. "odwołana" stawała się "odwoana" i marker
    # odwołanej lekcji nie był wykrywany.
    text = text.replace("ł", "l").replace("Ł", "L")
    return (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )


def _replacement_fields(info: Mapping[str, Any]) -> dict[str, str]:
    """Wyciagnij dane zastępstwa zapisane przez librus-apix."""
    result = {
        "subject_swap": "",
        "teacher_swap": "",
        "classroom_swap": "",
        "date_added": "",
    }
    for value in info.values():
        if not isinstance(value, Mapping):
            continue
        for key in result:
            candidate = value.get(key)
            if candidate not in (None, ""):
                result[key] = str(candidate).strip()
    return result


def normalize_period(period: Any, week_start: str = "") -> dict[str, Any] | None:
    """Zamien obiekt Period biblioteki na stabilny slownik integracji."""
    lesson_date = str(_get(period, "date", "")).strip()
    start = str(_get(period, "date_from", "")).strip()
    end = str(_get(period, "date_to", "")).strip()
    original_subject = str(_get(period, "subject", "")).strip()
    teacher_and_classroom = str(_get(period, "teacher_and_classroom", "")).strip()

    try:
        parsed_date = date.fromisoformat(lesson_date)
    except ValueError:
        return None

    # Biblioteka zwraca rowniez sobote i niedziele. Integracja szkolna celowo
    # ich nie publikuje i nie uzywa do budzika.
    if parsed_date.weekday() >= 5:
        return None

    raw_info = _get(period, "info", {})
    info = _json_safe(raw_info if isinstance(raw_info, Mapping) else {})
    replacements = _replacement_fields(info)
    info_text = _plain_text(info)
    cancelled = any(marker in info_text for marker in _CANCELLED_MARKERS)
    changed = bool(
        replacements["subject_swap"]
        or replacements["teacher_swap"]
        or replacements["classroom_swap"]
        or any(marker in info_text for marker in _CHANGED_MARKERS)
    )

    subject = replacements["subject_swap"] or original_subject
    teacher = replacements["teacher_swap"] or teacher_and_classroom
    room = replacements["classroom_swap"]
    number_raw = _get(period, "number", 0)
    try:
        number = int(number_raw)
    except (TypeError, ValueError):
        number = 0

    # Puste komorki tabeli nie sa lekcjami. Zachowujemy odwolane lekcje tylko
    # wtedy, gdy Librus nadal podaje ich przedmiot.
    if not subject:
        return None

    status = "cancelled" if cancelled else ("changed" if changed else "active")
    lesson_key = f"{lesson_date}|{number}|{start}"

    return {
        "lesson_key": lesson_key,
        "week_start": week_start or monday_for(parsed_date).isoformat(),
        "date": lesson_date,
        "weekday": WEEKDAYS_PL[parsed_date.weekday()],
        "number": number,
        "start": start,
        "end": end,
        "time": f"{start}-{end}" if start and end else start,
        "subject": subject,
        "original_subject": original_subject,
        "teacher": teacher,
        "room": room,
        "teacher_and_classroom": teacher_and_classroom,
        "status": status,
        "active": not cancelled,
        "cancelled": cancelled,
        "changed": changed,
        "info": info,
        "replacement": replacements,
    }


def normalize_timetable(raw_weeks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Znormalizuj dwa tygodnie i usun ewentualne duplikaty."""
    deduplicated: dict[str, dict[str, Any]] = {}
    for week in raw_weeks:
        week_start = str(week.get("week_start", ""))
        days = week.get("days", [])
        if not isinstance(days, Sequence):
            continue
        for periods in days:
            if not isinstance(periods, Sequence):
                continue
            for period in periods:
                lesson = normalize_period(period, week_start)
                if lesson is not None:
                    deduplicated[lesson["lesson_key"]] = lesson
    return sorted(
        deduplicated.values(),
        key=lambda item: (item["date"], item["start"], item["number"]),
    )


def active_lessons(lessons: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Zwroc tylko aktywne lekcje."""
    return [dict(lesson) for lesson in lessons if lesson.get("active", False)]


def lessons_for_date(
    lessons: Iterable[Mapping[str, Any]], target: date, *, active_only: bool = False
) -> list[dict[str, Any]]:
    """Zwroc lekcje z jednego dnia."""
    result = [
        dict(lesson)
        for lesson in lessons
        if lesson.get("date") == target.isoformat()
        and (not active_only or lesson.get("active", False))
    ]
    return sorted(
        result, key=lambda item: (item.get("start", ""), item.get("number", 0))
    )


def first_active_lesson(
    lessons: Iterable[Mapping[str, Any]], target: date
) -> dict[str, Any] | None:
    """Zwroc pierwsza aktywna lekcje danego dnia."""
    day = lessons_for_date(lessons, target, active_only=True)
    return day[0] if day else None


def current_active_lesson(
    lessons: Iterable[Mapping[str, Any]], now: datetime
) -> dict[str, Any] | None:
    """Zwróć aktywną lekcję trwającą dokładnie w tej chwili."""
    comparison_now = now.replace(tzinfo=None) if now.tzinfo else now
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for lesson in active_lessons(lessons):
        try:
            start = datetime.fromisoformat(f"{lesson['date']}T{lesson['start']}")
            end = datetime.fromisoformat(f"{lesson['date']}T{lesson['end']}")
        except (KeyError, TypeError, ValueError):
            continue
        if start <= comparison_now < end:
            candidates.append((start, lesson))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def next_active_lesson(
    lessons: Iterable[Mapping[str, Any]], now: datetime
) -> dict[str, Any] | None:
    """Zwroc najblizsza aktywna lekcje, ktora jeszcze sie nie skonczyla."""
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for lesson in active_lessons(lessons):
        try:
            end = datetime.fromisoformat(f"{lesson['date']}T{lesson['end']}")
            start = datetime.fromisoformat(f"{lesson['date']}T{lesson['start']}")
        except (KeyError, TypeError, ValueError):
            continue
        comparison_now = now.replace(tzinfo=None) if now.tzinfo else now
        if end > comparison_now:
            candidates.append((start, lesson))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def attach_schedule_events(
    lessons: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Powiąż terminarz z planem bez zgadywania przy niejednoznacznych danych.

    Reguły są celowo deterministyczne:
    1. data musi być identyczna,
    2. numer lekcji ma pierwszeństwo,
    3. bez numeru dopasowujemy tylko jednoznaczny przedmiot tego dnia,
    4. niejednoznaczne wpisy zostają na liście niedopasowanych.
    """
    enriched = [dict(lesson) for lesson in lessons]
    for lesson in enriched:
        lesson["wydarzenia"] = []

    unmatched: list[dict[str, Any]] = []
    by_date: dict[str, list[dict[str, Any]]] = {}
    for lesson in enriched:
        lesson_date = str(lesson.get("date", "") or "")
        if lesson_date:
            by_date.setdefault(lesson_date, []).append(lesson)

    for raw_event in events:
        event = dict(raw_event)
        event_date = str(event.get("data", "") or "")
        candidates = by_date.get(event_date, [])
        if not candidates:
            unmatched.append(event)
            continue

        number_raw = event.get("numer_lekcji")
        try:
            number = int(number_raw) if number_raw not in (None, "") else None
        except (TypeError, ValueError):
            number = None

        picked: dict[str, Any] | None = None
        if number is not None:
            numbered = [
                lesson
                for lesson in candidates
                if int(lesson.get("number", 0) or 0) == number
            ]
            if len(numbered) == 1:
                picked = numbered[0]
            elif len(numbered) > 1:
                event_subject = _plain_text(event.get("przedmiot", ""))
                subject_matches = [
                    lesson
                    for lesson in numbered
                    if event_subject
                    and _plain_text(lesson.get("subject", "")) == event_subject
                ]
                if len(subject_matches) == 1:
                    picked = subject_matches[0]

        if picked is None and number is None:
            event_subject = _plain_text(event.get("przedmiot", ""))
            if event_subject:
                subject_matches = [
                    lesson
                    for lesson in candidates
                    if _plain_text(lesson.get("subject", "")) == event_subject
                ]
                if len(subject_matches) == 1:
                    picked = subject_matches[0]

        if picked is None:
            unmatched.append(event)
            continue
        picked["wydarzenia"].append(event)

    return enriched, unmatched


def timetable_hours(lessons: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """Zbuduj czytelna mape numerow i godzin lekcji."""
    result: dict[str, dict[str, str]] = {}
    for lesson in sorted(lessons, key=lambda item: item.get("number", 0)):
        number = str(lesson.get("number", 0))
        if number == "0" or number in result:
            continue
        start = str(lesson.get("start", ""))
        end = str(lesson.get("end", ""))
        result[number] = {
            "start": start,
            "end": end,
            "time": f"{start}-{end}" if start and end else start,
        }
    return result


def lessons_by_date(
    lessons: Iterable[Mapping[str, Any]], *, active_only: bool = False
) -> dict[str, list[dict[str, Any]]]:
    """Pogrupuj lekcje wedlug daty."""
    result: dict[str, list[dict[str, Any]]] = {}
    for lesson in lessons:
        if active_only and not lesson.get("active", False):
            continue
        key = str(lesson.get("date", ""))
        if not key:
            continue
        result.setdefault(key, []).append(dict(lesson))
    for day in result.values():
        day.sort(key=lambda item: (item.get("start", ""), item.get("number", 0)))
    return result
