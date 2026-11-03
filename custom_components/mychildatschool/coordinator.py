"""Coordinator for MyChildAtSchool."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AttendanceDay, MCASAuthError, MCASClient, MCASError, Student
from .const import (
    AUTH_FAILURES_BEFORE_REAUTH,
    CONF_END_TIME,
    CONF_START_TIME,
    DEFAULT_END_TIME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_START_TIME,
    DOMAIN,
    SCHOOL_DAY,
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


def next_poll(
    now: datetime,
    start: time,
    end: time,
    interval: timedelta,
    is_school_day: Callable[[date], bool],
) -> datetime:
    """When to poll next: every `interval` between `start` and `end` on school days.

    Outside the window the next poll is `start` on the next school day. A long
    holiday is searched past; if the calendar somehow has no school day in the
    next couple of months, the next weekday is used rather than going quiet.
    """
    today = now.date()
    if is_school_day(today):
        opens = datetime.combine(today, start, tzinfo=now.tzinfo)
        if now < opens:
            return opens
        if now + interval <= datetime.combine(today, end, tzinfo=now.tzinfo):
            return now + interval
    for offset in range(1, 61):
        day = today + timedelta(days=offset)
        if is_school_day(day):
            return datetime.combine(day, start, tzinfo=now.tzinfo)
    day = today + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, start, tzinfo=now.tzinfo)


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

    Polling only runs between the configured start and end times on school days.
    Fetching and summarising are kept apart so that "today" can move on at midnight
    from cached data, without a poll.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: MCASClient,
        options: Mapping[str, Any] | None = None,
    ) -> None:
        """Set up the coordinator, the polling window and the per-day caches."""
        options = options or {}
        self._interval = timedelta(
            minutes=options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL.total_seconds() / 60
            )
        )
        self._start = dt_util.parse_time(
            options.get(CONF_START_TIME, DEFAULT_START_TIME)
        )
        self._end = dt_util.parse_time(options.get(CONF_END_TIME, DEFAULT_END_TIME))
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=self._interval)
        self.client = client
        self.modules: dict[str, bool] | None = None
        # Read once per load; a child added to the account appears on reload.
        self.students: list[Student] | None = None
        self._auth_failures = 0
        self._attendance: dict[int, dict[date, AttendanceDay]] = {}
        self._behaviour: dict[int, dict[date, list[dict]]] = {}
        # Latest non-daily data per child, kept so summaries can be rebuilt.
        self._latest: dict[int, dict[str, Any]] = {}
        # ISO date -> day type, merged across children's schools.
        self._calendar: dict[str, str] = {}

    def is_school_day(self, day: date) -> bool:
        """School day per the calendar MCAS publishes; weekdays when it is silent.

        The calendar only covers the current academic year, and is empty until the
        first fetch, hence the fallback.
        """
        kind = self._calendar.get(day.isoformat())
        if kind is None:
            return day.weekday() < 5
        return kind == SCHOOL_DAY

    def schedule_next(self, now: datetime) -> None:
        """Point the next refresh at the next slot in the polling window."""
        self.update_interval = (
            next_poll(now, self._start, self._end, self._interval, self.is_school_day)
            - now
        )

    async def _async_update_data(self) -> dict:
        # HA reads update_interval after every refresh, failed or not, so setting
        # it up front keeps retries inside the window too. The calendar used here
        # is from the previous fetch, which is fine: it rarely changes.
        self.schedule_next(dt_util.now())
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

    @staticmethod
    def _window(today: date) -> list[date]:
        """School days to summarise: weekdays within the lookback window."""
        days = [today - timedelta(days=i) for i in range(WEEK_LOOKBACK_DAYS)]
        return [d for d in days if d.weekday() < 5]

    def _fetch(self) -> dict:
        client = self.client
        client.ensure_session()
        if self.modules is None:
            self.modules = client.modules()
        if self.students is None:
            self.students = client.students()
        today = dt_util.now().date()
        for student in self.students:
            self._fetch_student(student, today)
        return self.summarise_all(today)

    def _fetch_student(self, student: Student, today: date) -> None:
        """Call the portal for one child and keep the raw results."""
        client = self.client
        sid = student.id
        attendance_cache = self._attendance.setdefault(sid, {})
        behaviour_cache = self._behaviour.setdefault(sid, {})
        volatile = {today, today - timedelta(days=1)}

        for day in self._window(today):
            if day in attendance_cache and day not in volatile:
                continue
            attendance_cache[day] = client.attendance(sid, day)
            behaviour_cache[day] = client.behaviour(sid, day)

        behaviour_year = client.behaviour_year(sid)
        for day, kind in behaviour_year["calendar"].items():
            # A school day at any child's school counts.
            if kind == SCHOOL_DAY or day not in self._calendar:
                self._calendar[day] = kind
        modules = self.modules
        self._latest[sid] = {
            "behaviour_year": behaviour_year,
            "timetable": client.timetable(student),
            "reports": client.reports(sid) if modules.get("reports") else [],
            "clubs_and_trips": (
                client.clubs_and_trips(sid)
                if modules.get("clubs") or modules.get("trips")
                else []
            ),
            "detentions": client.detentions(sid),
            "dinner_balance": (
                client.dinner_balance(sid) if modules.get("dinner") else None
            ),
        }

    def summarise_all(self, today: date | None = None) -> dict:
        """Build every child's sensor data from what has been fetched. No calls."""
        today = today or dt_util.now().date()
        return {
            "students": {
                student.id: self._summarise(student, today)
                for student in self.students or []
                if student.id in self._latest
            }
        }

    def _summarise(self, student: Student, today: date) -> dict:
        sid = student.id
        latest = self._latest[sid]
        attendance_cache = self._attendance.get(sid, {})
        behaviour_cache = self._behaviour.get(sid, {})

        window = self._window(today)
        attendance = [attendance_cache[d] for d in window if d in attendance_cache]
        recorded = [a for a in attendance if a.periods]
        present = [a for a in recorded if a.present]
        events: list[dict] = []
        for day in window:
            events.extend(behaviour_cache.get(day, []))

        behaviour_year = latest["behaviour_year"]
        calendar = behaviour_year["calendar"]
        timetable = latest["timetable"]
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
            "behaviour_year_name": behaviour_year.get("year_name"),
            "day_type": calendar.get(today_iso),
            "next_school_day": next(
                (
                    d
                    for d in sorted(calendar)
                    if d > today_iso and calendar[d] == SCHOOL_DAY
                ),
                None,
            ),
            "timetable": timetable,
            "lessons_today": lessons_today,
            "next_lesson": upcoming[0] if upcoming else None,
            "reports": latest["reports"],
            "clubs_and_trips": latest["clubs_and_trips"],
            "detentions": latest["detentions"],
            "dinner_balance": latest["dinner_balance"],
        }
