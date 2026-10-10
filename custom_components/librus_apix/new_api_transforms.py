"""Adapt new-api Librus models into Smart Home With Me's established data shapes.

No HA, network, or third-party imports: this is deliberately testable in isolation.
Missing or forbidden API sections must be handled by the caller, not invented here.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Mapping


def display(mapping: Mapping[Any, str] | None, identifier: Any) -> str:
    """Map an API identifier to its real display label."""
    if identifier is None:
        return ""
    if not mapping:
        return ""
    return str(mapping.get(identifier, "") or "")


def student_info(snapshot: Any) -> SimpleNamespace:
    """Match the attribute-based student object expected by existing entities."""
    me = snapshot.me
    school_class = snapshot.school_class
    school = snapshot.school
    lucky = snapshot.lucky_number
    return SimpleNamespace(
        name=me.display_name,
        class_name=school_class.display_name if school_class else "",
        number=None,  # The API requires a separate student_number() request.
        tutor=display(
            snapshot.teachers,
            school_class.tutor_id if school_class else None,
        ),
        school=school.name if school else "",
        lucky_number=lucky.number if lucky else None,
    )


def grades(snapshot: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    categories = snapshot.grade_categories
    for grade in snapshot.grades:
        category = categories.get(grade.category_id)
        result.append(
            {
                "subject": display(snapshot.subjects, grade.subject_id) or "Nieznany",
                "grade": str(grade.value or ""),
                "display_grade": str(grade.value or ""),
                "date": str(grade.add_date or ""),
                "category": category.name if category else "",
                "comment": "\n".join(grade.comments),
                "teacher": display(snapshot.teachers, grade.teacher_id),
                "semester": grade.semester,
                "weight": category.weight if category else None,
                "counts_to_average": category.count_to_average if category else None,
                "href": f"new:{grade.id}",
                "type": "numeric",
            }
        )

    for grade in snapshot.descriptive_grades:
        result.append(
            {
                "subject": display(snapshot.subjects, grade.subject_id) or "Nieznany",
                "grade": str(grade.value or ""),
                "display_grade": str(grade.value or ""),
                "date": str(grade.add_date or grade.date or ""),
                "category": str(grade.skill or ""),
                "comment": "\n".join(grade.comments),
                "teacher": display(snapshot.teachers, grade.teacher_id),
                "semester": grade.semester,
                "href": f"new:descriptive:{grade.id}",
                "type": "descriptive_text",
            }
        )

    for grade in snapshot.behaviour_grades:
        result.append(
            {
                "subject": "Zachowanie",
                "grade": grade.display,
                "date": str(grade.add_date or ""),
                "category": grade.name or "",
                "comment": "\n".join(grade.comments),
                "teacher": display(snapshot.teachers, grade.teacher_id),
                "semester": grade.semester,
                "type": "behavior",
            }
        )
    return result


def notes(snapshot: Any) -> list[dict[str, str]]:
    categories = snapshot.note_categories
    return [
        {
            "content": str(note.text or ""),
            "date": str(note.date or ""),
            "author": display(snapshot.teachers, note.teacher_id),
            "type": note.sentiment or "",
            "category": str(categories.get(note.category_id, "") or ""),
        }
        for note in snapshot.notes
    ]


def messages(snapshot: Any, *, count: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "author": str(message.sender_name or ""),
            "title": str(message.topic or ""),
            "date": str(message.send_date or ""),
            "href": str(message.id),
            "unread": message.read_date is None,
            "has_attachment": bool(message.has_attachment),
        }
        for message in snapshot.messages[:count]
    ]


def homework(snapshot: Any) -> list[SimpleNamespace]:
    result = []
    for task in snapshot.homework_assignments:
        due = str(task.due_date or "")
        if not due:
            continue
        result.append(
            SimpleNamespace(
                subject="",  # New HomeWorkAssignments has no subject field.
                category=snapshot.homework_categories.get(task.category_id, ""),
                teacher=display(snapshot.teachers, task.teacher_id),
                lesson=task.topic,
                task_date=str(task.date or ""),
                completion_date=due,
                href=str(task.id),
            )
        )
    return result


def agenda(snapshot: Any) -> list[dict[str, Any]]:
    result = []
    for event in snapshot.homeworks:
        if not event.date:
            continue
        result.append(
            {
                "data": str(event.date),
                "tydzien": "",
                "tytul": str(event.content or ""),
                "przedmiot": display(snapshot.subjects, event.subject_id),
                "godzina": str(event.time_from or ""),
                "numer_lekcji": None,
                "szczegoly": str(event.content or ""),
                "href": f"new/{event.id}",
            }
        )
    return sorted(result, key=lambda item: item["data"])


def attendance(snapshot: Any) -> list[dict[str, Any]]:
    result = []
    for entry in snapshot.attendances:
        kind = snapshot.attendance_types.get(entry.type_id)
        if not kind:
            symbol = "?"  # Unknown types must not become fake absences.
            label = ""
        else:
            label = str(kind.name or "")
            folded = label.casefold()
            if "spóź" in folded or "spoz" in folded:
                symbol = "sp"
            elif kind.is_presence_kind:
                symbol = "ob"
            elif kind.is_excused_absence:
                symbol = "u"
            else:
                symbol = "nb"
        result.append(
            {
                "symbol": symbol,
                "typ": label,
                "data": str(entry.date or ""),
                "przedmiot": "",  # The API requires a separate lesson-id lookup.
                "nauczyciel": "",
                "godzina": entry.lesson_no,
            }
        )
    return result


def announcements(snapshot: Any) -> list[dict[str, str]]:
    return [
        {
            "tytul": str(item.subject or ""),
            "nadawca": "",
            "opis": str(item.content or ""),
            "data": str(item.creation_date or item.start_date or ""),
        }
        for item in snapshot.school_notices
    ]


def timetable_week(
    monday: date,
    lessons_by_date: Mapping[date, list[Any]],
    subjects: Mapping[Any, str],
    teachers: Mapping[Any, str],
    classrooms: Mapping[Any, str],
) -> dict[str, Any]:
    """Convert either regular or kindergarten blocks into a seven-day week."""
    days: list[list[dict[str, Any]]] = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        periods: list[dict[str, Any]] = []
        for lesson in lessons_by_date.get(day, []):
            teacher_ids = getattr(lesson, "teacher_ids", ())
            teacher = display(teachers, lesson.teacher_id)
            if not teacher and teacher_ids:
                teacher = ", ".join(
                    name for identifier in teacher_ids
                    if (name := display(teachers, identifier))
                )
            room = display(classrooms, lesson.classroom_id)
            changed = bool(lesson.is_substitution)
            cancelled = bool(lesson.is_canceled)
            periods.append(
                {
                    "date": day.isoformat(),
                    "date_from": str(lesson.hour_from or ""),
                    "date_to": str(lesson.hour_to or ""),
                    "subject": display(subjects, lesson.subject_id)
                    or "Zajęcia przedszkolne",
                    "teacher": teacher,
                    "room": room,
                    "teacher_and_classroom": " / ".join(
                        p for p in (teacher, room) if p
                    ),
                    "number": lesson.lesson_no or 0,
                    "cancelled": cancelled,
                    "changed": changed,
                    "info": {
                        **({"odwołana": True} if cancelled else {}),
                        **({"zastępstwo": True} if changed else {}),
                        **(
                            {"uwaga": str(lesson.substitution_note)}
                            if lesson.substitution_note else {}
                        ),
                    },
                }
            )
        days.append(periods)
    return {"week_start": monday.isoformat(), "days": days}
