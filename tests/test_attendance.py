"""Tests for attendance classification without Home Assistant runtime."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "librus_apix"
    / "attendance.py"
)
SPEC = importlib.util.spec_from_file_location("librus_attendance_test_module", MODULE_PATH)
assert SPEC and SPEC.loader
attendance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(attendance)


class AttendanceClassificationTests(unittest.TestCase):
    def test_unexcused_absence(self) -> None:
        entry = {"symbol": "nb", "typ": "Nieobecność"}
        self.assertTrue(attendance.is_absence(entry))
        self.assertTrue(attendance.is_unexcused_absence(entry))
        self.assertFalse(attendance.is_excused_absence(entry))

    def test_excused_absence(self) -> None:
        entry = {"symbol": "u", "typ": "Nieobecność uspr."}
        self.assertTrue(attendance.is_absence(entry))
        self.assertTrue(attendance.is_excused_absence(entry))
        self.assertFalse(attendance.is_unexcused_absence(entry))

    def test_release_is_not_unexcused(self) -> None:
        entry = {"symbol": "zw", "typ": "Zwolnienie"}
        self.assertTrue(attendance.is_absence(entry))
        self.assertTrue(attendance.is_excused_absence(entry))
        self.assertFalse(attendance.is_unexcused_absence(entry))

    def test_lateness_is_separate(self) -> None:
        entry = {"symbol": "sp", "typ": "Spóźnienie"}
        self.assertTrue(attendance.is_late(entry))
        self.assertFalse(attendance.is_absence(entry))

    def test_id_is_stable_for_same_record(self) -> None:
        entry = {
            "symbol": "nb",
            "typ": "Nieobecność",
            "data": "2026-10-06",
            "przedmiot": "Matematyka",
            "nauczyciel": "Jan Kowalski",
            "godzina": 3,
        }
        self.assertEqual(attendance.attendance_id(entry), attendance.attendance_id(dict(entry)))


if __name__ == "__main__":
    unittest.main()
