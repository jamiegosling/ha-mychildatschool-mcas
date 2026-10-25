"""Client for the MyChildAtSchool (Bromcom MCAS) parent portal.

MCAS is an ASP.NET WebForms site, but the pages talk to a real ``api/v1`` REST
backend through a single generic proxy web service. Authenticating is therefore a
WebForms form post, after which every data call is a POST to that proxy carrying
the backend path. See const.py for the routes.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from .const import (
    BASE_URL,
    DASHBOARD_MARKER,
    DAY_STATUS,
    EP_ATTENDANCE,
    EP_BEHAVIOUR,
    EP_BEHAVIOUR_DETAIL,
    EP_CLUBS,
    EP_CONFIGURATIONS,
    EP_DETENTIONS,
    EP_DINNER,
    EP_REPORTS,
    EP_STUDENT_YEARS,
    EP_USER_DETAILS,
    LOGIN_PATH,
    MODULE_FLAGS,
    PAGE_TIMETABLE,
    PRESENT_MARK_SIGNS,
    PROXY_PATH,
)

_LOGGER = logging.getLogger(__name__)

# The proxy reports a bad path as HTTP 200 with this string in "d", so it has to be
# sniffed rather than caught as an error.
_NOT_FOUND = "Error 404"
_HIDDEN_RE = re.compile(r'<input[^>]*type="hidden"[^>]*>', re.I)
_NAME_RE = re.compile(r'name="([^"]+)"', re.I)
_VALUE_RE = re.compile(r'value="([^"]*)"', re.I)
_STUDENT_NAME_RE = re.compile(r'id="[^"]*StudentName[^"]*"[^>]*>([^<]{2,80})', re.I)
_MASTER_VAR_RE = re.compile(r"var\s+(master_[A-Za-z]+)\s*=\s*\"?([^\";\n]{0,80})")


_DAY_HEADER_RE = re.compile(
    r"([A-Z][a-z]+)\s*(\d{1,2})(?:st|nd|rd|th)?\s*([A-Z][a-z]{2})"
)


def _parse_day_header(header: str):
    """Turn a timetable column header into a day name and ISO date.

    "Monday14th Sep" becomes ("Monday", "2026-09-14"). The header carries no year,
    so the nearest one within six months of today is assumed.
    """
    match = _DAY_HEADER_RE.search(header or "")
    if not match:
        return (header, None)
    name, day, month = match.groups()
    today = date.today()
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            parsed = _dt.datetime.strptime(f"{day} {month} {year}", "%d %b %Y").date()
        except ValueError:
            continue
        if abs((parsed - today).days) <= 180:
            return (name, parsed.isoformat())
    return (name, None)


def _as_int(value) -> int | None:
    """Totals arrive as strings, and "N/A" when the school hides that figure."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class MCASAuthError(Exception):
    """Credentials were rejected."""


class MCASError(Exception):
    """Any other failure talking to MCAS."""


@dataclass
class AttendanceDay:
    """One school day's registration marks."""

    day: date
    periods: list[dict] = field(default_factory=list)

    @property
    def present(self) -> bool | None:
        """True if every recorded period counts as in school. None if no marks."""
        if not self.periods:
            return None
        return all(p.get("MarkSign") in PRESENT_MARK_SIGNS for p in self.periods)

    @property
    def summary(self) -> str:
        """Human-readable list of the day's marks, one per period."""
        if not self.periods:
            return "No data"
        return ", ".join(
            f"{p.get('PeriodName')}: {p.get('MarkMeaning')}" for p in self.periods
        )


@dataclass
class Student:
    """One pupil attached to the parent account."""

    id: int
    name: str | None = None
    school_id: int | None = None
    school_name: str | None = None


class MCASClient:
    """Synchronous MCAS client. Run it in an executor from Home Assistant."""

    def __init__(self, session, email: str, password: str) -> None:
        """Store the session and credentials; no network access happens here."""
        self._s = session
        self._email = email
        self._password = password
        self.student_id: int | None = None
        self.school_id: int | None = None
        self.school_name: str | None = None
        self.student_name: str | None = None
        # The parent's contact id. The timetable page needs it alongside the
        # student id to know which child to render.
        self.contact_id: int | None = None
        self._year_ids: dict[int, int] = {}

    # ---------------------------------------------------------------- auth

    def login(self) -> None:
        """Perform the WebForms login and capture the pupil context."""
        url = f"{BASE_URL}{LOGIN_PATH}"
        page = self._s.get(url, timeout=30)
        form = self._hidden_fields(page.text)
        if not form:
            raise MCASError("Login page had no hidden fields; layout may have changed")
        form["EmailTextBox"] = self._email
        form["PasswordTextBox"] = self._password
        resp = self._s.post(url, data=form, timeout=30)
        if DASHBOARD_MARKER not in resp.url:
            raise MCASAuthError(self._describe_login_failure(resp))
        self._read_dashboard_context(resp.text)

    @staticmethod
    def _describe_login_failure(resp) -> str:
        """Explain *why* a login did not reach the dashboard.

        MCAS answers every outcome with HTTP 200 and simply renders a different
        page, so the landing URL is the only signal. Reporting "check your
        password" for all of them is misleading: a correct password can still be
        held short by a terms-and-conditions page or a security-question setup,
        which is exactly what a school can force after a password reset.
        """
        landed = (resp.url or "").rsplit("/", 1)[-1].split("?")[0]
        interstitials = {
            "MCSTermsAndConditions": (
                "MyChildAtSchool is asking you to accept its terms and conditions. "
                "Sign in at mychildatschool.com once and accept them, then retry."
            ),
            "MCSParentSecurityQuestions": (
                "MyChildAtSchool is asking you to set security questions. "
                "Sign in at mychildatschool.com once and complete them, then retry."
            ),
            "MCSAccountSettings": (
                "MyChildAtSchool redirected to account settings, which usually "
                "means it wants something completed on the account. Sign in "
                "there once, then retry."
            ),
        }
        for page, message in interstitials.items():
            if page.lower() in (resp.url or "").lower():
                return message

        # Surface any validation text the login page rendered.
        text = ""
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            for node in soup.select(
                "[id*=Error], [id*=Message], [id*=Validation], .alert"
            ):
                candidate = node.get_text(" ", strip=True)
                if candidate and len(candidate) < 200:
                    text = candidate
                    break
        except Exception:  # never let diagnostics mask the auth failure
            pass

        if text:
            return f"Login rejected by MyChildAtSchool: {text}"
        if landed and landed != "MCSParentLogin":
            return (
                f"Login did not reach the dashboard - MyChildAtSchool sent us to "
                f"'{landed}'. Sign in at mychildatschool.com to see what it wants."
            )
        return "Login failed - check the email address and password"

    @staticmethod
    def _hidden_fields(html: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for tag in _HIDDEN_RE.findall(html):
            name = _NAME_RE.search(tag)
            if not name:
                continue
            value = _VALUE_RE.search(tag)
            out[name.group(1)] = value.group(1) if value else ""
        return out

    def _read_dashboard_context(self, html: str) -> None:
        """Pull ids and the default pupil's name straight off the dashboard.

        The page defines master_studentid / master_schoolID / master_userid as plain
        JS vars. master_userid is the parent's contact id, which nothing in the API
        returns but the timetable page requires. The student is only the child the
        dashboard shows by default; students() lists every child on the account.
        """
        variables = dict(_MASTER_VAR_RE.findall(html))
        sid = variables.get("master_studentid")
        if not sid:
            # Every call needs a student id, and None would quietly end up in
            # every path. Fail the login instead.
            raise MCASError("Dashboard had no student id; layout may have changed")
        self.student_id = int(sid)
        if contact := variables.get("master_userid"):
            self.contact_id = int(contact)
        if school := variables.get("master_schoolID"):
            self.school_id = int(school)
        self.school_name = variables.get("master_schoolName")
        if match := _STUDENT_NAME_RE.search(html):
            name = match.group(1).strip()
            # Rendered "Surname, Forename" in the header.
            if "," in name:
                surname, _, forename = name.partition(",")
                name = f"{forename.strip()} {surname.strip()}".strip()
            self.student_name = name

    # ---------------------------------------------------------------- core

    def _get(self, path: str, _retry: bool = True):
        """Call a backend api/v1 path through the portal's proxy.

        An expired session is not reported as a 401 or a redirect to the login
        page - the proxy raises server-side and returns **HTTP 500** with a generic
        ASP.NET error body. That is indistinguishable from a real server fault, so
        the only reliable response is to log in again and retry once: if the retry
        succeeds the session had simply lapsed, and if the fresh login fails the
        credentials are genuinely wrong.
        """
        resp = self._s.post(
            f"{BASE_URL}{PROXY_PATH}",
            json={"url": path, "schoolID": "", "contactID": ""},
            headers={"Content-Type": "application/json;charset=utf-8"},
            timeout=30,
        )
        if resp.status_code == 401 or DASHBOARD_MARKER in resp.text[:200]:
            raise MCASAuthError("Session expired")
        if resp.status_code == 500 and _retry:
            _LOGGER.debug("Proxy returned 500; re-authenticating and retrying once")
            self.login()  # raises MCASAuthError if the credentials no longer work
            return self._get(path, _retry=False)
        if resp.status_code != 200:
            raise MCASError(f"{path} -> HTTP {resp.status_code}")
        payload = resp.json().get("d")
        if payload in (None, ""):
            return None
        if isinstance(payload, str) and payload.startswith(_NOT_FOUND):
            # Wrong path, or the wrong number of path parameters.
            # Redact ids: paths embed the student and school id, and debug logs
            # get pasted into issue reports.
            _LOGGER.debug(
                "MCAS reported no such route: %s", re.sub(r"\d{3,}", "<id>", path)
            )
            return None
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            return payload  # an HTML fragment

    def ensure_session(self) -> None:
        """Re-login if the session has lapsed."""
        if self.student_id is None:
            self.login()

    # ------------------------------------------------------------ fetchers

    def user_details(self) -> dict | None:
        """The signed-in parent and the pupils attached to the account."""
        return self._get(EP_USER_DETAILS)

    def students(self) -> list[Student]:
        """Every pupil on the account.

        Falls back to the dashboard's default pupil if user/details returns
        nothing usable, so a single-child account keeps working regardless.
        """
        data = self.user_details()
        rows = data.get("Students") if isinstance(data, dict) else None
        found = []
        for row in rows or []:
            if not row.get("ID"):
                continue
            first = row.get("PreferredFirstName") or row.get("FirstName") or ""
            last = row.get("PreferredLastName") or row.get("LastName") or ""
            found.append(
                Student(
                    id=int(row["ID"]),
                    name=f"{first} {last}".strip() or None,
                    school_id=row.get("SchoolID"),
                    school_name=row.get("SchoolName"),
                )
            )
        if found:
            return found
        _LOGGER.debug("user/details listed no students; using the dashboard pupil")
        return [
            Student(
                id=self.student_id,
                name=self.student_name,
                school_id=self.school_id,
                school_name=self.school_name,
            )
        ]

    def load_year_id(self, sid: int) -> int | None:
        """Current academic YearID, needed by the behaviour endpoint."""
        data = self._get(EP_STUDENT_YEARS.format(sid=sid))
        rows = (data or {}).get("Table") or []
        if rows:
            self._year_ids[sid] = rows[0].get("YearID")
        return self._year_ids.get(sid)

    def _year_id(self, sid: int) -> int | None:
        if sid not in self._year_ids:
            self.load_year_id(sid)
        return self._year_ids.get(sid)

    def attendance(self, sid: int, day: date) -> AttendanceDay:
        """Registration marks for one day. MCAS serves a single day per call."""
        data = self._get(
            EP_ATTENDANCE.format(sid=sid, y=day.year, m=day.month, d=day.day)
        )
        rows = (data or {}).get("Table") or [] if isinstance(data, dict) else []
        return AttendanceDay(day=day, periods=rows)

    def behaviour(self, sid: int, day: date) -> list[dict]:
        """Behaviour events for one day. Returned as an HTML table, so parsed."""
        html = self._get(
            EP_BEHAVIOUR.format(
                sid=sid,
                yid=self._year_id(sid),
                y=day.year,
                m=day.month,
                d=day.day,
            )
        )
        return self._parse_behaviour(html)

    @staticmethod
    def _parse_behaviour(html) -> list[dict]:
        if not html or not isinstance(html, str):
            return []
        soup = BeautifulSoup(html, "html.parser")
        headers = [th.get_text(strip=True) for th in soup.select("thead th")]
        events: list[dict] = []
        for row in soup.select("tbody tr"):
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if not cells:
                continue
            events.append(
                dict(zip(headers, cells, strict=False))
                if headers
                else {"Event": cells[-1]}
            )
        return events

    def behaviour_year(self, sid: int) -> dict:
        """Whole-year behaviour in one call: points, events and a subject lookup.

        Strongly preferred over walking eventstable day by day - it is JSON rather
        than an HTML fragment, it is one request instead of one per day, and it is
        the only source of the points totals. `Adjustment` is the points value for
        an event; summing it reproduces the "Overall Total Points" figure shown on
        the portal's behaviour page.
        """
        data = self._get(EP_BEHAVIOUR_DETAIL.format(sid=sid, yid=self._year_id(sid)))
        if not isinstance(data, dict):
            return {"events": [], "points": {}, "subjects": {}, "calendar": {}}

        subjects = {
            row.get("SubjectID"): row.get("SubjectName")
            for row in data.get("Table3") or []
        }
        events = []
        for row in data.get("Table") or []:
            events.append(
                {
                    "date": row.get("EventDate"),
                    "type": row.get("EventType"),
                    "points": row.get("Adjustment") or 0,
                    "subject": subjects.get(row.get("SubjectID")),
                    "id": row.get("EventRecordID"),
                }
            )
        events.sort(key=lambda e: e["date"] or "", reverse=True)

        calendar = self.calendar(data.get("Table1"))
        totals = (data.get("Table4") or [{}])[0]
        positive = sum(e["points"] for e in events if e["type"] == "Positive")
        negative = sum(e["points"] for e in events if e["type"] == "Negative")
        return {
            "events": events,
            "subjects": subjects,
            "calendar": calendar,
            "year_name": ((data.get("Table2") or [{}])[0]).get("YearName"),
            "points": {
                "total": positive - abs(negative),
                "positive": positive,
                "negative": abs(negative),
                "all_time_total": _as_int(totals.get("ShowTotalPointsAllTime")),
                "all_time_positive": _as_int(totals.get("PositivePointsAllTime")),
                "all_time_negative": _as_int(totals.get("NegativePointsAllTime")),
            },
        }

    def modules(self) -> dict[str, bool]:
        """Which MCAS modules this school has switched on.

        Used to avoid creating entities that can only ever read zero. Values come
        back as the strings "True"/"False" with inconsistent casing.
        """
        data = self._get(EP_CONFIGURATIONS)
        rows = (data or {}).get("Table") or [] if isinstance(data, dict) else []
        config = {r.get("KeyName"): str(r.get("KeyValue")) for r in rows}
        enabled = {}
        for name, key in MODULE_FLAGS.items():
            enabled[name] = config.get(key, "").strip().lower() == "true"
        return enabled

    def calendar(self, table1: list[dict]) -> dict[str, str]:
        """Map ISO date -> day type from the behaviour payload's calendar table."""
        out: dict[str, str] = {}
        for row in table1 or []:
            day = (row.get("Day") or "")[:10]
            if day:
                out[day] = DAY_STATUS.get(row.get("DayStatusCode"), "Unknown")
        return out

    def timetable(self, student: Student) -> list[dict]:
        """Parse the rendered weekly timetable grid.

        There is no API route for this - MCSTimetable is server-rendered - so the
        grid is read from the page. Each cell is a stack of divs (period, school,
        subject, class, teacher) whose `title` attributes hold the untruncated text,
        which is what gets used; the visible text is ellipsised.

        Without the query string the page renders whichever child the session has
        selected, so the child is always named explicitly. All three ids are
        required.
        """
        if self.contact_id is None or student.school_id is None:
            # Better no timetable than another child's.
            _LOGGER.debug("No contact or school id; skipping the timetable")
            return []
        resp = self._s.get(
            f"{BASE_URL}{PAGE_TIMETABLE}",
            params={
                "SchoolID": student.school_id,
                "StudentID": student.id,
                "ContactID": self.contact_id,
            },
            timeout=30,
        )
        if "MCSParentLogin" in resp.url:
            # Bounced to the login page: the session has lapsed.
            raise MCASAuthError("Session expired")
        soup = BeautifulSoup(resp.text, "html.parser")
        lessons: list[dict] = []
        for table in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
            if not headers or not any(
                d in " ".join(headers) for d in ("Monday", "Tuesday")
            ):
                continue
            days = [_parse_day_header(h) for h in headers]
            for row in table.find_all("tr")[1:]:
                for index, cell in enumerate(row.find_all("td")):
                    if index >= len(days):
                        break
                    divs = cell.find_all("div")
                    if len(divs) < 3:
                        continue
                    values = [
                        d.get("title") or d.get_text(" ", strip=True) for d in divs
                    ]
                    period, subject = values[0], values[2] if len(values) > 2 else None
                    if not subject:
                        continue
                    lessons.append(
                        {
                            "day": days[index][0],
                            "date": days[index][1],
                            "period": period,
                            "subject": subject,
                            "class": values[3] if len(values) > 3 else None,
                            "teacher": values[4] if len(values) > 4 else None,
                        }
                    )
            break
        return lessons

    def reports(self, sid: int) -> list[dict]:
        """School reports published to the parent."""
        data = self._get(EP_REPORTS.format(sid=sid))
        return (data or {}).get("Table") or [] if isinstance(data, dict) else []

    def clubs_and_trips(self, sid: int) -> list[dict]:
        """Clubs and trips the pupil is enrolled on."""
        data = self._get(EP_CLUBS.format(sid=sid))
        return (data or {}).get("Table") or [] if isinstance(data, dict) else []

    def detentions(self, sid: int) -> list[dict]:
        """Detentions recorded for the pupil."""
        data = self._get(EP_DETENTIONS.format(sid=sid))
        if not isinstance(data, dict):
            return []
        return data.get("Detention") or []

    def dinner_balance(self, sid: int) -> float | None:
        """Credit balance, scraped from the dashboard widget's HTML."""
        html = self._get(EP_DINNER.format(sid=sid))
        if not html or not isinstance(html, str):
            return None
        match = re.search(r"£\s*(-?[\d,]+\.\d{2})", html)
        return float(match.group(1).replace(",", "")) if match else None
