"""Tests for pure timetable logic without Home Assistant runtime."""

from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "librus_apix"
    / "timetable.py"
)
SPEC = importlib.util.spec_from_file_location("librus_timetable_test_module", MODULE_PATH)
assert SPEC and SPEC.loader
timetable = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timetable)


class CurrentLessonTests(unittest.TestCase):
    def test_returns_lesson_happening_now(self) -> None:
        lessons = [
            {
                "date": "2026-10-06",
                "start": "09:00",
                "end": "09:45",
                "subject": "Matematyka",
                "active": True,
            },
            {
                "date": "2026-10-06",
                "start": "10:00",
                "end": "10:45",
                "subject": "Polski",
                "active": True,
            },
        ]
        current = timetable.current_active_lesson(
            lessons, datetime(2026, 10, 6, 9, 20)
        )
        self.assertIsNotNone(current)
        self.assertEqual(current["subject"], "Matematyka")

    def test_ignores_cancelled_lesson(self) -> None:
        lessons = [
            {
                "date": "2026-10-06",
                "start": "09:00",
                "end": "09:45",
                "subject": "Matematyka",
                "active": False,
                "cancelled": True,
            }
        ]
        self.assertIsNone(
            timetable.current_active_lesson(
                lessons, datetime(2026, 10, 6, 9, 20)
            )
        )

    def test_break_returns_none(self) -> None:
        lessons = [
            {
                "date": "2026-10-06",
                "start": "09:00",
                "end": "09:45",
                "subject": "Matematyka",
                "active": True,
            },
            {
                "date": "2026-10-06",
                "start": "10:00",
                "end": "10:45",
                "subject": "Polski",
                "active": True,
            },
        ]
        self.assertIsNone(
            timetable.current_active_lesson(
                lessons, datetime(2026, 10, 6, 9, 50)
            )
        )


class ScheduleMatchingTests(unittest.TestCase):
    def test_matches_exact_lesson_number_first(self) -> None:
        lessons = [
            {
                "date": "2026-10-06",
                "number": 3,
                "start": "10:00",
                "end": "10:45",
                "subject": "Matematyka",
                "active": True,
            },
            {
                "date": "2026-10-06",
                "number": 4,
                "start": "10:55",
                "end": "11:40",
                "subject": "Polski",
                "active": True,
            },
        ]
        events = [
            {
                "data": "2026-10-06",
                "numer_lekcji": 4,
                "przedmiot": "Polski",
                "tytul": "Sprawdzian",
            }
        ]
        enriched, unmatched = timetable.attach_schedule_events(lessons, events)
        self.assertEqual(unmatched, [])
        self.assertEqual(enriched[0]["wydarzenia"], [])
        self.assertEqual(enriched[1]["wydarzenia"][0]["tytul"], "Sprawdzian")

    def test_matches_unique_subject_when_number_missing(self) -> None:
        lessons = [
            {
                "date": "2026-10-06",
                "number": 1,
                "subject": "Język polski",
                "active": True,
            },
            {
                "date": "2026-10-06",
                "number": 2,
                "subject": "Matematyka",
                "active": True,
            },
        ]
        events = [
            {
                "data": "2026-10-06",
                "przedmiot": "Matematyka",
                "tytul": "Kartkówka",
            }
        ]
        enriched, unmatched = timetable.attach_schedule_events(lessons, events)
        self.assertEqual(unmatched, [])
        self.assertEqual(enriched[1]["wydarzenia"][0]["tytul"], "Kartkówka")

    def test_keeps_ambiguous_event_unmatched(self) -> None:
        lessons = [
            {
                "date": "2026-10-06",
                "number": 1,
                "subject": "Angielski",
                "active": True,
            },
            {
                "date": "2026-10-06",
                "number": 5,
                "subject": "Angielski",
                "active": True,
            },
        ]
        events = [
            {
                "data": "2026-10-06",
                "przedmiot": "Angielski",
                "tytul": "Odpowiedź ustna",
            }
        ]
        enriched, unmatched = timetable.attach_schedule_events(lessons, events)
        self.assertTrue(all(not lesson["wydarzenia"] for lesson in enriched))
        self.assertEqual(len(unmatched), 1)


if __name__ == "__main__":
    unittest.main()
