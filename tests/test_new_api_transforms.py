"""Contract tests for the optional current-API backend's normalizer.

Uses lightweight fake typed models so a real school account and HA are not needed.
"""

from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace as N

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "librus_apix" / "new_api_transforms.py"
)
SPEC = importlib.util.spec_from_file_location("librus_new_api_transforms_test", SOURCE)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

TIMETABLE_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "librus_apix" / "timetable.py"
)
TIMETABLE_SPEC = importlib.util.spec_from_file_location(
    "librus_new_api_timetable_test", TIMETABLE_SOURCE
)
assert TIMETABLE_SPEC and TIMETABLE_SPEC.loader
timetable = importlib.util.module_from_spec(TIMETABLE_SPEC)
TIMETABLE_SPEC.loader.exec_module(timetable)


class KindergartenTests(unittest.TestCase):
    def test_preschool_blocks_have_real_subjects_even_without_lesson_number(self):
        monday = date(2026, 10, 5)
        block = N(
            lesson_no=None,
            hour_from="07:30",
            hour_to="09:00",
            subject_id="LID-ACTIVITY-1",
            teacher_id=None,
            teacher_ids=("LID-TEACHER-1",),
            classroom_id="LID-ROOM-1",
            is_canceled=False,
            is_substitution=False,
            substitution_note=None,
        )
        week = module.timetable_week(
            monday,
            {monday: [block]},
            {"LID-ACTIVITY-1": "Edukacja przedszkolna"},
            {"LID-TEACHER-1": "Anna Nowak"},
            {"LID-ROOM-1": "Sala Motylków"},
        )
        self.assertEqual(len(week["days"]), 7)
        period = week["days"][0][0]
        self.assertEqual(period["subject"], "Edukacja przedszkolna")
        self.assertEqual(period["teacher"], "Anna Nowak")
        self.assertEqual(period["room"], "Sala Motylków")
        self.assertEqual(period["number"], 0)
        self.assertEqual(period["date_from"], "07:30")

    def test_normalizer_preserves_room_and_active_state(self):
        monday = date(2026, 10, 5)
        block = N(
            lesson_no=None, hour_from="07:30", hour_to="09:00",
            subject_id=1, teacher_id=2, teacher_ids=(),
            classroom_id=3, is_canceled=False, is_substitution=False,
            substitution_note=None
        )
        week = module.timetable_week(
            monday, {monday:[block]}, {1:"Rytmika"}, {2:"Pani Nowak"}, {3:"12"}
        )
        result = timetable.normalize_timetable([week])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "active")
        self.assertEqual(result[0]["teacher"], "Pani Nowak")
        self.assertEqual(result[0]["room"], "12")

    def test_parallel_preschool_blocks_do_not_overwrite_each_other(self):
        monday = date(2026, 10, 5)
        a = N(
            lesson_no=None, hour_from="07:30", hour_to="09:00",
            subject_id=1, teacher_id=2, teacher_ids=(),
            classroom_id=3, is_canceled=False, is_substitution=False,
            substitution_note=None
        )
        b = N(
            lesson_no=None, hour_from="07:30", hour_to="09:00",
            subject_id=4, teacher_id=5, teacher_ids=(),
            classroom_id=3, is_canceled=False, is_substitution=False,
            substitution_note=None
        )
        week=module.timetable_week(
            monday, {monday:[a,b]}, {1:"Rytmika",4:"Sport"},
            {2:"Anna Nowak",5:"Jan Test"}, {3:"12"}
        )
        lessons=timetable.normalize_timetable([week])
        self.assertEqual(len(lessons),2)
        self.assertNotEqual(lessons[0]["lesson_key"],lessons[1]["lesson_key"])

    def test_cancellations_and_substitutions_are_preserved(self):
        monday = date(2026, 10, 5)
        block = N(
            lesson_no=2, hour_from="09:00", hour_to="09:45",
            subject_id=4, teacher_id=7, teacher_ids=(),
            classroom_id=9, is_canceled=True, is_substitution=True,
            substitution_note="Zastępstwo",
        )
        entry=module.timetable_week(
            monday, {monday:[block]}, {4:"Matematyka"}, {7:"Nauczyciel"}, {9:"9"}
        )["days"][0][0]
        self.assertTrue(entry["cancelled"])
        self.assertTrue(entry["changed"])
        self.assertEqual(entry["number"], 2)


class DataMappingTests(unittest.TestCase):
    def test_grade_category_weight_is_preserved(self):
        grade=N(
            id=17, value="5", category_id=2, subject_id=7, semester=1,
            add_date="2026-10-08", comments=["Brawo"], teacher_id=3
        )
        snap=N(
            grades=[grade], descriptive_grades=[], behaviour_grades=[],
            grade_categories={2:N(name="Sprawdzian", weight=4.0, count_to_average=True)},
            subjects={7:"Matematyka"}, teachers={3:"Pani Nowak"}
        )
        item=module.grades(snap)[0]
        self.assertEqual(item["weight"],4.0)
        self.assertTrue(item["counts_to_average"])
        self.assertEqual(item["comment"],"Brawo")

    def test_unsupported_attendance_code_is_not_declared_absent(self):
        snap=N(
            attendances=[N(type_id="unknown",date="2026-10-09", lesson_no=3)],
            attendance_types={}
        )
        self.assertEqual(module.attendance(snap)[0]["symbol"],"?")

    def test_message_list_does_not_open_or_mark_read(self):
        snap=N(messages=[
            N(id="X2", sender_name="Nauczyciel", topic="Test",
              send_date="2026-10-09", read_date=None, has_attachment=False)
        ])
        item=module.messages(snap)[0]
        self.assertTrue(item["unread"])
        self.assertEqual(item["href"],"X2")

    def test_school_class_and_student_name(self):
        snap=N(
            me=N(display_name="Ola Testowa"),
            school_class=N(display_name="Motylki",tutor_id="T1"),
            school=N(name="Przedszkole nr 1"),
            lucky_number=None,
            teachers={"T1":"Anna Nowak"}
        )
        info=module.student_info(snap)
        self.assertEqual(info.class_name,"Motylki")
        self.assertEqual(info.tutor,"Anna Nowak")


if __name__ == "__main__":
    unittest.main()
