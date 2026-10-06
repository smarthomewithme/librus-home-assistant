"""Tests for grade math without Home Assistant runtime."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "librus_apix"
    / "grade_math.py"
)
SPEC = importlib.util.spec_from_file_location("librus_grade_math_test_module", MODULE_PATH)
assert SPEC and SPEC.loader
grade_math = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grade_math)


class GradeValueTests(unittest.TestCase):
    def test_plus_and_minus(self) -> None:
        self.assertEqual(grade_math.grade_value("4+"), 4.5)
        self.assertEqual(grade_math.grade_value("3-"), 2.75)

    def test_non_numeric_marker_is_ignored(self) -> None:
        self.assertIsNone(grade_math.grade_value("T · 15/21 pkt"))
        self.assertIsNone(grade_math.grade_value("bz"))


class GradeAverageTests(unittest.TestCase):
    def test_arithmetic_average_skips_not_counted_grade(self) -> None:
        grades = [
            {"ocena": "5", "liczy_do_sredniej": True},
            {"ocena": "1", "liczy_do_sredniej": False},
            {"ocena": "3", "liczy_do_sredniej": True},
        ]
        self.assertEqual(grade_math.average_grades(grades), 4.0)

    def test_weighted_average_uses_librus_weights(self) -> None:
        grades = [
            {"ocena": "5", "waga": 3, "liczy_do_sredniej": True},
            {"ocena": "2", "waga": 1, "liczy_do_sredniej": True},
        ]
        self.assertEqual(grade_math.average_grades(grades, weighted=True), 4.25)

    def test_missing_weight_defaults_to_one(self) -> None:
        grades = [
            {"ocena": "4", "liczy_do_sredniej": True},
            {"ocena": "2", "waga": None, "liczy_do_sredniej": True},
        ]
        self.assertEqual(grade_math.average_grades(grades, weighted=True), 3.0)


if __name__ == "__main__":
    unittest.main()
