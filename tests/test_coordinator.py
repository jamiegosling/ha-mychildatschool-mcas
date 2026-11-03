"""Tests for the pieces of coordinator logic that are easy to get wrong."""

from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mychildatschool.api import AttendanceDay, MCASError, Student
from custom_components.mychildatschool.coordinator import (
    MCASCoordinator,
    _period_order,
    next_poll,
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


MON = date(2026, 10, 26)


def _weekdays(day):
    return day.weekday() < 5


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Inside the window: just wait the interval.
        (datetime(2026, 10, 26, 9, 0), datetime(2026, 10, 26, 9, 30)),
        # The last slot that still lands by the end time.
        (datetime(2026, 10, 26, 17, 30), datetime(2026, 10, 26, 18, 0)),
        # Past the end: the next school day's start.
        (datetime(2026, 10, 26, 17, 45), datetime(2026, 10, 27, 7, 0)),
        # Before the start: today's start, not tomorrow's.
        (datetime(2026, 10, 26, 3, 0), datetime(2026, 10, 26, 7, 0)),
        # Friday evening and the weekend both go to Monday.
        (datetime(2026, 10, 30, 19, 0), datetime(2026, 11, 2, 7, 0)),
        (datetime(2026, 10, 31, 12, 0), datetime(2026, 11, 2, 7, 0)),
    ],
)
def test_next_poll_keeps_to_the_window(now, expected):
    assert (
        next_poll(now, time(7), time(18), timedelta(minutes=30), _weekdays) == expected
    )


def test_next_poll_skips_a_holiday():
    holiday = {MON + timedelta(days=i) for i in range(5)}
    assert next_poll(
        datetime(2026, 10, 23, 19, 0),
        time(7),
        time(18),
        timedelta(minutes=30),
        lambda d: _weekdays(d) and d not in holiday,
    ) == datetime(2026, 11, 2, 7, 0)


def test_next_poll_never_goes_quiet():
    """A calendar with no school days at all still polls on the next weekday."""
    assert next_poll(
        datetime(2026, 10, 30, 19, 0),
        time(7),
        time(18),
        timedelta(minutes=30),
        lambda d: False,
    ) == datetime(2026, 11, 2, 7, 0)


async def test_school_days_come_from_the_calendar(hass):
    coordinator = MCASCoordinator(hass, MagicMock())
    # Unknown dates fall back to weekdays.
    assert coordinator.is_school_day(MON)
    assert not coordinator.is_school_day(MON + timedelta(days=5))
    coordinator._calendar = {MON.isoformat(): "Holiday"}
    assert not coordinator.is_school_day(MON)


async def test_options_set_the_window(hass):
    coordinator = MCASCoordinator(
        hass,
        MagicMock(),
        {"scan_interval": 15, "start_time": "08:00:00", "end_time": "16:00:00"},
    )
    coordinator.schedule_next(datetime(2026, 10, 26, 7, 0))
    assert coordinator.update_interval == timedelta(hours=1)
    coordinator.schedule_next(datetime(2026, 10, 26, 9, 0))
    assert coordinator.update_interval == timedelta(minutes=15)


async def test_update_schedules_the_next_poll_before_fetching(hass):
    """Even a failed poll has to wait for the window, not retry overnight."""
    coordinator = MCASCoordinator(hass, MagicMock())
    with (
        patch.object(coordinator, "_fetch", side_effect=MCASError("down")),
        patch.object(coordinator, "schedule_next") as schedule_next,
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
    schedule_next.assert_called_once()


async def test_today_moves_on_without_polling(hass):
    """Nights are not polled, so midnight must re-summarise from the cache."""
    client = MagicMock()
    client.modules.return_value = {}
    client.students.return_value = [Student(id=1, name="A")]
    client.attendance.return_value = AttendanceDay(day=None, periods=[])
    client.behaviour.return_value = []
    client.behaviour_year.return_value = {
        "calendar": {"2026-10-26": "School day", "2026-10-27": "Holiday"},
        "events": [],
        "points": {},
        "year_name": None,
    }
    client.timetable.return_value = [
        {"date": "2026-10-26", "period": "1", "subject": "Maths"},
        {"date": "2026-10-27", "period": "1", "subject": "Art"},
    ]
    client.detentions.return_value = []
    coordinator = MCASCoordinator(hass, client)
    coordinator.students = client.students()
    coordinator.modules = {}
    coordinator._fetch_student(coordinator.students[0], MON)
    client.reset_mock()

    tuesday = coordinator.summarise_all(MON + timedelta(days=1))["students"][1]

    assert [x["subject"] for x in tuesday["lessons_today"]] == ["Art"]
    assert tuesday["day_type"] == "Holiday"
    assert not client.mock_calls
