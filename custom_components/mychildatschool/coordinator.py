"""Coordinator for MyChildAtSchool."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AttendanceDay, MCASAuthError, MCASClient, MCASError, Student
from .const import (
    AUTH_FAILURES_BEFORE_REAUTH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    WEEK_LOOKBACK_DAYS,
)

_LOGGER = logging.getLogger(__name__)


def _period_order(period) -> tuple[int, str]:
    """Sort periods chronologically.

    Periods are labelled "Tutor", "1", "2"... so a plain string sort puts Tutor
    last when it is actually morning registration, and would order period 10 before
    period 2. Numbered periods sort numerically after Tutor.
    """
    text = str(period or "").strip()
    if text.isdigit():
        return (1, f"{int(text):03d}")
    if text.lower().startswith("tutor"):
        return (0, "")
    return (2, text.lower())


class MCASCoordinator(DataUpdateCoordinator):
    """Fetch every pupil's data over one shared login, keeping calls to a minimum.

    MCAS serves attendance and behaviour one day at a time, so a week costs a call
    per day. Past school days do not change, so they are cached for the life of the
    entry and only today and yesterday are re-fetched (yesterday because registers
    are sometimes corrected the next morning). Steady state is therefore a handful
    of calls per poll rather than one per day of the week.

    One login covers every child on the account, and the per-child endpoints are
    keyed by student id, so the session and the school-wide module flags are shared
    and only the per-child calls are repeated.
    """

    def __init__(self, hass: HomeAssistant, client: MCASClient) -> None:
        """Set up the coordinator and the per-day caches."""
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL
        )
        self.client = client
        self.modules: dict[str, bool] | None = None
        # Read once per load; a child added to the account appears on reload.
        self.students: list[Student] | None = None
        self._auth_failures = 0
        self._attendance: dict[int, dict[date, AttendanceDay]] = {}
        self._behaviour: dict[int, dict[date, list[dict]]] = {}

    async def _async_update_data(self) -> dict:
        try:
            data = await self.hass.async_add_executor_job(self._fetch)
        except MCASAuthError as err:
            self._auth_failures += 1
            # Don't demand re-authentication on the strength of one failure.
            # MCAS answers every outcome with HTTP 200 and a rendered page, so a
            # timeout or a momentary blip is indistinguishable from a bad
            # password. Raising ConfigEntryAuthFailed immediately stops polling
            # and waits for a human, which left the integration dead for hours
            # after a single hiccup that the next poll would have cleared.
            if self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(
                f"{err} (attempt {self._auth_failures} of "
                f"{AUTH_FAILURES_BEFORE_REAUTH} before asking you to sign in again)"
            ) from err
        except MCASError as err:
            raise UpdateFailed(str(err)) from err
        self._auth_failures = 0
        return data

    def _window(self) -> list[date]:
        """School days to summarise: weekdays within the lookback window."""
        today = date.today()
        days = [today - timedelta(days=i) for i in range(WEEK_LOOKBACK_DAYS)]
        return [d for d in days if d.weekday() < 5]

    def _fetch(self) -> dict:
        client = self.client
        client.ensure_session()
        if self.modules is None:
            self.modules = client.modules()
        if self.students is None:
            self.students = client.students()
        return {
            "students": {
                student.id: self._fetch_student(student) for student in self.students
            }
        }

    def _fetch_student(self, student: Student) -> dict:
        client = self.client
        sid = student.id
        attendance_cache = self._attendance.setdefault(sid, {})
        behaviour_cache = self._behaviour.setdefault(sid, {})
        today = date.today()
        volatile = {today, today - timedelta(days=1)}

        for day in self._window():
            if day in attendance_cache and day not in volatile:
                continue
            attendance_cache[day] = client.attendance(sid, day)
            behaviour_cache[day] = client.behaviour(sid, day)

        window = self._window()
        attendance = [attendance_cache[d] for d in window if d in attendance_cache]
        recorded = [a for a in attendance if a.periods]
        present = [a for a in recorded if a.present]
        events: list[dict] = []
        for day in window:
            events.extend(behaviour_cache.get(day, []))

        behaviour_year = client.behaviour_year(sid)
        calendar = behaviour_year["calendar"]
        timetable = client.timetable(student)
        today_iso = today.isoformat()
        lessons_today = [x for x in timetable if x.get("date") == today_iso]
        # No lesson times are published, only period labels, so "next" is the first
        # lesson of the next timetabled day rather than the next period from now.
        upcoming = sorted(
            (x for x in timetable if (x.get("date") or "") > today_iso),
            key=lambda x: (x["date"], _period_order(x.get("period"))),
        )

        return {
            "student_name": student.name,
            "school_name": student.school_name,
            "attendance_today": attendance_cache.get(today),
            "attendance_days": recorded,
            "attendance_pct": (
                round(len(present) / len(recorded) * 100, 1) if recorded else None
            ),
            # Recent events carry the human-readable level ("Lesson Mark 1: Above
            # Expected Attitude and Behaviour") which only the HTML view exposes.
            "behaviour_events": events,
            # Points, totals and the subject breakdown come from the year-wide JSON
            # call, which is both richer and cheaper than walking days.
            "behaviour_points": behaviour_year["points"],
            "behaviour_year_events": behaviour_year["events"],
            "behaviour_year_name": behaviour_year["year_name"],
            "day_type": calendar.get(today_iso),
            "next_school_day": next(
                (
                    d
                    for d in sorted(calendar)
                    if d > today_iso and calendar[d] == "School day"
                ),
                None,
            ),
            "timetable": timetable,
            "lessons_today": lessons_today,
            "next_lesson": upcoming[0] if upcoming else None,
            "reports": client.reports(sid) if self.modules.get("reports") else [],
            "clubs_and_trips": (
                client.clubs_and_trips(sid)
                if self.modules.get("clubs") or self.modules.get("trips")
                else []
            ),
            "detentions": client.detentions(sid),
            "dinner_balance": (
                client.dinner_balance(sid) if self.modules.get("dinner") else None
            ),
        }
