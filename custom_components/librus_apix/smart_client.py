"""Extended Librus client features used by Smart Home With Me."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from librus_apix.grades import get_grades
from librus_apix.helpers import no_access_check

from .api import (
    LibrusApiClient,
    _grade_comment,
    _grade_counts_to_average,
    _grade_weight,
    _is_behavior_subject,
    _is_grade,
    current_semester,
)

_LOGGER = logging.getLogger(__name__)

_SCORE_SUM_RE = re.compile(
    r"Suma\s+punkt[oó]w\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*p?\.?\s*/\s*"
    r"([0-9]+(?:[.,][0-9]+)?)\s*p?\.?",
    re.IGNORECASE,
)


def _label(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().rstrip(":").split())


def _grade_detail_fields(html: str) -> dict[str, str]:
    """Parse the grade detail page into simple label/value fields."""
    soup = no_access_check(BeautifulSoup(html or "", "lxml"))
    result: dict[str, str] = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        key = _label(cells[0].get_text(" ", strip=True))
        if not key:
            continue
        value = cells[1].get_text("\n", strip=True)
        if value:
            result[key] = value

    return result


def _display_special_grade(raw_grade: str, detail_value: str) -> str:
    """Build a compact display value without pretending a point result is 1-6."""
    raw = str(raw_grade or "").strip()
    details = str(detail_value or "").strip()
    if not details:
        return raw

    match = _SCORE_SUM_RE.search(details)
    if match:
        got = match.group(1).replace(",", ".")
        maximum = match.group(2).replace(",", ".")
        return f"{raw} · {got}/{maximum} pkt" if raw else f"{got}/{maximum} pkt"

    first_line = next((line.strip() for line in details.splitlines() if line.strip()), "")
    if first_line and first_line != raw and len(first_line) <= 48:
        return f"{raw} · {first_line}" if raw else first_line
    return raw


class SmartHomeLibrusApiClient(LibrusApiClient):
    """Librus client with stable grade hrefs and point-grade details."""

    def __init__(self, username: str, password: str) -> None:
        super().__init__(username, password)
        self._grade_details_cache: dict[str, dict[str, str]] = {}

    async def _async_grade_details(self, href: str) -> dict[str, str]:
        href = str(href or "").strip()
        if not href:
            return {}
        if href in self._grade_details_cache:
            return dict(self._grade_details_cache[href])
        if self._client is None:
            return {}

        if href.startswith("http"):
            url = href
        else:
            url = f"{self._client.BASE_URL.rstrip('/')}/{href.lstrip('/')}"
        try:
            response = await self._run_blocking(self._client.get, url)
            fields = _grade_detail_fields(response.text)
        except Exception as err:  # detail must never break the main grade list
            _LOGGER.warning("Nie udało się pobrać szczegółów oceny %s: %s", href, err)
            return {}

        self._grade_details_cache[href] = dict(fields)
        return fields

    async def async_get_grades(self) -> list[dict[str, Any]] | None:
        """Fetch current-semester grades and preserve stable grade hrefs.

        Non-standard grade markers such as ``T`` are resolved on demand through
        the grade-detail page. The raw marker stays available, while dashboards
        receive a compact display value plus the full point-result text.
        """

        async def request() -> list[dict[str, Any]]:
            numeric, _, descriptive = await self._run_blocking(
                get_grades, self._client, "all"
            )
            semester = current_semester()
            result: list[dict[str, Any]] = []

            for subject_grades in numeric or []:
                for subject, grades in subject_grades.items():
                    for grade in grades:
                        if grade.semester != semester:
                            continue

                        raw_grade = str(grade.grade or "").strip()
                        href = str(getattr(grade, "href", "") or "").strip()
                        entry_type = (
                            "behavior_current"
                            if _is_behavior_subject(subject)
                            else "numeric"
                        )

                        detail_fields: dict[str, str] = {}
                        detail_grade = ""
                        display_grade = raw_grade
                        if entry_type == "numeric" and not _is_grade(raw_grade) and href:
                            detail_fields = await self._async_grade_details(href)
                            detail_grade = str(detail_fields.get("ocena", "") or "").strip()
                            display_grade = _display_special_grade(raw_grade, detail_grade)

                        result.append(
                            {
                                "subject": subject,
                                "grade": raw_grade,
                                "display_grade": display_grade,
                                "grade_details": detail_grade,
                                "details": detail_fields,
                                "href": href,
                                "date": grade.date,
                                "category": (
                                    detail_fields.get("kategoria")
                                    or grade.category
                                ),
                                "comment": _grade_comment(grade),
                                "teacher": (
                                    detail_fields.get("nauczyciel")
                                    or getattr(grade, "teacher", "")
                                ),
                                "semester": grade.semester,
                                "weight": _grade_weight(grade),
                                "counts_to_average": _grade_counts_to_average(grade),
                                "type": entry_type,
                            }
                        )

            for subject_grades in descriptive or []:
                for subject, grades in subject_grades.items():
                    for grade in grades:
                        value = str(grade.grade or "").strip()
                        if grade.semester != semester:
                            continue
                        description = str(getattr(grade, "desc", "") or "")
                        title = str(getattr(grade, "title", "") or subject or "").strip()
                        href = str(getattr(grade, "href", "") or "").strip()

                        if _is_behavior_subject(subject) or _is_behavior_subject(title):
                            result.append(
                                {
                                    "subject": subject or title or "Zachowanie",
                                    "grade": value,
                                    "display_grade": value,
                                    "grade_details": "",
                                    "details": {},
                                    "href": href,
                                    "date": str(getattr(grade, "date", "") or ""),
                                    "category": title if title != subject else "",
                                    "comment": description,
                                    "teacher": getattr(grade, "teacher", ""),
                                    "semester": grade.semester,
                                    "type": "behavior",
                                }
                            )
                            continue

                        result.append(
                            {
                                "subject": subject,
                                "grade": value,
                                "display_grade": value,
                                "grade_details": "",
                                "details": {},
                                "href": href,
                                "date": grade.date,
                                "category": description.splitlines()[0]
                                if description
                                else "",
                                "comment": description,
                                "teacher": getattr(grade, "teacher", ""),
                                "semester": grade.semester,
                                "type": (
                                    "descriptive"
                                    if _is_grade(value)
                                    else "descriptive_text"
                                ),
                            }
                        )

            return result

        return await self._call_with_retry("ocen", request)
