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


if __name__ == "__main__":
    unittest.main()
