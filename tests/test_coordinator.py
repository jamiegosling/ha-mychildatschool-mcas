"""Tests for the pieces of coordinator logic that are easy to get wrong."""

from unittest.mock import MagicMock

import pytest

from custom_components.mychildatschool.api import AttendanceDay, Student
from custom_components.mychildatschool.coordinator import (
    MCASCoordinator,
    _period_order,
)


@pytest.mark.parametrize(
    ("periods", "expected"),
    [
        (["2", "Tutor", "10", "1"], ["Tutor", "1", "2", "10"]),
        (["10", "9"], ["9", "10"]),
        (["1", "Registration", "Tutor"], ["Tutor", "1", "Registration"]),
    ],
)
def test_period_order(periods, expected):
    """Tutor is morning registration, and period 10 comes after period 2.

    Sorting the labels as plain strings gets both of these wrong.
    """
    assert sorted(periods, key=_period_order) == expected


def test_attendance_day_present_requires_every_period():
    day = AttendanceDay(
        day=None,
        periods=[{"MarkSign": "P"}, {"MarkSign": "L"}],
    )
    assert day.present is True

    partial = AttendanceDay(day=None, periods=[{"MarkSign": "P"}, {"MarkSign": "N"}])
    assert partial.present is False


def test_attendance_day_with_no_marks_is_unknown_not_absent():
    """Weekends, holidays and pre-registration mornings must not read as absent."""
    assert AttendanceDay(day=None, periods=[]).present is None
    assert AttendanceDay(day=None, periods=[]).summary == "No data"


def test_attendance_summary_lists_each_period():
    day = AttendanceDay(
        day=None,
        periods=[
            {"PeriodName": "AM", "MarkMeaning": "Present"},
            {"PeriodName": "PM", "MarkMeaning": "Late"},
        ],
    )
    assert day.summary == "AM: Present, PM: Late"


async def test_fetch_covers_every_child_over_one_session(hass):
    """Each child gets its own data and caches; the account-wide calls run once."""
    client = MagicMock()
    client.modules.return_value = {}
    client.students.return_value = [Student(id=1, name="A"), Student(id=2, name="B")]
    # Only child 1 has marks, so a shared cache would leak them into child 2.
    client.attendance.side_effect = lambda sid, day: AttendanceDay(
        day=day, periods=[{"MarkSign": "P"}] if sid == 1 else []
    )
    client.behaviour.return_value = []
    client.behaviour_year.return_value = {
        "calendar": {},
        "events": [],
        "points": {},
        "year_name": None,
    }
    client.timetable.return_value = []
    client.detentions.return_value = []
    coordinator = MCASCoordinator(hass, client)

    data = coordinator._fetch()
    coordinator._fetch()

    assert set(data["students"]) == {1, 2}
    assert data["students"][1]["student_name"] == "A"
    assert data["students"][1]["attendance_days"]
    assert data["students"][2]["attendance_days"] == []
    assert client.modules.call_count == 1
    assert client.students.call_count == 1
    assert {c.args[0].id for c in client.timetable.call_args_list} == {1, 2}
