"""Tests for the MCAS client: WebForms login, the .asmx proxy, and the parsers."""

import json

import pytest
import requests
import responses
from responses import matchers

from custom_components.mychildatschool.api import (
    MCASAuthError,
    MCASClient,
    MCASError,
    Student,
    _parse_day_header,
)
from custom_components.mychildatschool.const import (
    BASE_URL,
    LOGIN_PATH,
    PAGE_TIMETABLE,
    PROXY_PATH,
)

# Fabricated - must match the values used in conftest.py's fixtures.
STUDENT_ID = 99999
SECOND_STUDENT_ID = 99998
SCHOOL_ID = 88888
CONTACT_ID = 12345
YEAR_ID = 40000

LOGIN_URL = f"{BASE_URL}{LOGIN_PATH}"
PROXY_URL = f"{BASE_URL}{PROXY_PATH}"
DASHBOARD_URL = f"{BASE_URL}/MCAS/MCSDashboardPage"
TIMETABLE_URL = f"{BASE_URL}{PAGE_TIMETABLE}"
STUDENT = Student(id=STUDENT_ID, name="Alex Smith", school_id=SCHOOL_ID)


@pytest.fixture
def client():
    return MCASClient(requests.Session(), "parent@example.com", "hunter2")


def _mock_login(rsps, login_html, dashboard_html):
    rsps.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    rsps.add(
        responses.POST,
        LOGIN_URL,
        body=dashboard_html,
        status=200,
        headers={"Location": DASHBOARD_URL},
    )


@responses.activate
def test_login_posts_hidden_fields_and_reads_context(
    client, login_html, dashboard_html
):
    """The WebForms hidden fields must be echoed back or the post is rejected."""
    responses.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    # A successful login 302s to the dashboard; landing there is the only success
    # signal MCAS gives, so the redirect has to be followed.
    responses.add(
        responses.POST,
        LOGIN_URL,
        status=302,
        headers={"Location": DASHBOARD_URL},
        body="",
    )
    responses.add(responses.GET, DASHBOARD_URL, body=dashboard_html, status=200)
    client.login()

    posted = next(c for c in responses.calls if c.request.method == "POST")
    assert "__VIEWSTATE=abc123" in posted.request.body
    assert "__EVENTVALIDATION=xyz789" in posted.request.body
    assert "EmailTextBox=parent%40example.com" in posted.request.body

    assert client.student_id == STUDENT_ID
    assert client.school_id == SCHOOL_ID
    assert client.school_name == "Example High School"
    # master_userid is the parent's contact id, which the timetable needs.
    assert client.contact_id == CONTACT_ID
    # The header renders "Surname, Forename"; entities should read naturally.
    assert client.student_name == "Alex Smith"


@responses.activate
def test_login_failure_raises_auth_error(client, login_html):
    """MCAS re-renders the login page rather than returning an error status."""
    responses.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    responses.add(responses.POST, LOGIN_URL, body=login_html, status=200)
    with pytest.raises(MCASAuthError):
        client.login()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (json.dumps({"Table": [{"a": 1}]}), {"Table": [{"a": 1}]}),
        ("<table><tr><td>x</td></tr></table>", "<table><tr><td>x</td></tr></table>"),
        ("", None),
        (None, None),
        # A bad path comes back as HTTP 200 with this string, not a 404 status.
        ("Error 404 - Requested API call reference Not Found.", None),
    ],
)
@responses.activate
def test_proxy_decodes_double_encoded_payloads(client, payload, expected):
    responses.add(responses.POST, PROXY_URL, json={"d": payload}, status=200)
    assert client._get("api/v1/anything") == expected


@responses.activate
def test_behaviour_year_computes_points_and_maps_subjects(client):
    """Summing Adjustment reproduces the total the portal shows."""
    client._year_ids[STUDENT_ID] = YEAR_ID
    responses.add(
        responses.POST,
        PROXY_URL,
        json={
            "d": json.dumps(
                {
                    "Table": [
                        {
                            "EventDate": "2026-09-11T09:00:00",
                            "EventType": "Positive",
                            "Adjustment": 2,
                            "SubjectID": 1,
                            "EventRecordID": 10,
                        },
                        {
                            "EventDate": "2026-09-10T09:00:00",
                            "EventType": "Neutral",
                            "Adjustment": 0,
                            "SubjectID": 2,
                            "EventRecordID": 11,
                        },
                        {
                            "EventDate": "2026-09-09T09:00:00",
                            "EventType": "Negative",
                            "Adjustment": -1,
                            "SubjectID": 1,
                            "EventRecordID": 12,
                        },
                    ],
                    "Table1": [
                        {"Day": "2026-09-11T00:00:00", "DayStatusCode": "-"},
                        {"Day": "2026-09-12T00:00:00", "DayStatusCode": "*"},
                        {"Day": "2026-09-14T00:00:00", "DayStatusCode": "#"},
                        {"Day": "2026-09-15T00:00:00", "DayStatusCode": "$"},
                    ],
                    "Table2": [{"YearName": "2026/2027"}],
                    "Table3": [
                        {"SubjectID": 1, "SubjectName": "Music"},
                        {"SubjectID": 2, "SubjectName": "Maths"},
                    ],
                    "Table4": {},
                }
            )
        },
        status=200,
    )
    result = client.behaviour_year(STUDENT_ID)
    assert result["year_name"] == "2026/2027"
    assert result["points"]["positive"] == 2
    assert result["points"]["negative"] == 1
    assert result["points"]["total"] == 1
    # Newest first, with SubjectID resolved to a name.
    assert result["events"][0]["subject"] == "Music"
    assert result["events"][0]["date"].startswith("2026-09-11")
    assert result["calendar"] == {
        "2026-09-11": "School day",
        "2026-09-12": "Weekend",
        "2026-09-14": "Holiday",
        "2026-09-15": "Staff day",
    }


@responses.activate
def test_all_time_totals_tolerate_na(client):
    """Schools can hide a figure, in which case the API returns the string "N/A"."""
    client._year_ids[STUDENT_ID] = YEAR_ID
    responses.add(
        responses.POST,
        PROXY_URL,
        json={
            "d": json.dumps(
                {
                    "Table": [],
                    "Table3": [],
                    "Table4": [
                        {
                            "ShowTotalPointsAllTime": "12",
                            "PositivePointsAllTime": "12",
                            "NegativePointsAllTime": "N/A",
                        }
                    ],
                }
            )
        },
        status=200,
    )
    points = client.behaviour_year(STUDENT_ID)["points"]
    assert points["all_time_total"] == 12
    assert points["all_time_negative"] is None


def test_parse_behaviour_reads_the_html_table(client, behaviour_html):
    events = client._parse_behaviour(behaviour_html)
    assert len(events) == 2
    assert events[0]["Subject"] == "Music"
    # The level wording exists only in the HTML view, not the JSON one.
    assert "Lesson Mark 1" in events[0]["Event"]


@pytest.mark.parametrize("value", [None, "", "<table></table>", 12345])
def test_parse_behaviour_handles_junk(client, value):
    assert client._parse_behaviour(value) == []


@responses.activate
def test_timetable_prefers_title_attributes(client, timetable_html):
    """Visible cell text is ellipsised; `title` holds the real value."""
    client.contact_id = CONTACT_ID
    responses.add(responses.GET, TIMETABLE_URL, body=timetable_html, status=200)
    lessons = client.timetable(STUDENT)
    assert len(lessons) == 4
    tutor = [x for x in lessons if x["period"] == "Tutor"]
    assert tutor[0]["subject"] == "Tutor Period"
    assert tutor[0]["teacher"] == "Ms Example"
    assert tutor[0]["day"] == "Monday"
    assert {x["subject"] for x in lessons} == {"Tutor Period", "Rel. Stud.", "Drama"}


@responses.activate
def test_modules_parses_string_booleans(client):
    """Flags come back as the strings "True"/"False" with inconsistent casing."""
    responses.add(
        responses.POST,
        PROXY_URL,
        json={
            "d": json.dumps(
                {
                    "Table": [
                        {"KeyName": "MCASAttendanceModuleEnabled", "KeyValue": "True"},
                        {"KeyName": "MCASBehaviourModuleEnabled", "KeyValue": "true"},
                        {
                            "KeyName": "MCASDinnerMoneyModule_EnableDinnerMoneyModule",
                            "KeyValue": "False",
                        },
                        {"KeyName": "MCASClubsModuleEnabled", "KeyValue": "false"},
                    ]
                }
            )
        },
        status=200,
    )
    modules = client.modules()
    assert modules["attendance"] is True
    assert modules["behaviour"] is True
    assert modules["dinner"] is False
    assert modules["clubs"] is False
    # A flag the school never sets should not be reported as enabled.
    assert modules["trips"] is False


@responses.activate
@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<span>Credit Balance Summary : £ 12.34</span>", 12.34),
        ("<span>£ -5.00</span>", -5.0),
        ("<span>£ 1,234.56</span>", 1234.56),
        ("<span>no balance published</span>", None),
    ],
)
def test_dinner_balance_scrapes_the_widget(client, html, expected):
    responses.add(responses.POST, PROXY_URL, json={"d": html}, status=200)
    assert client.dinner_balance(STUDENT_ID) == expected


@pytest.mark.parametrize(
    ("header", "day"),
    [
        ("Monday14th Sep", "Monday"),
        ("Friday1st Nov", "Friday"),
        ("nonsense", "nonsense"),
    ],
)
def test_parse_day_header(header, day):
    assert _parse_day_header(header)[0] == day


@responses.activate
def test_expired_session_relogins_and_retries(client, login_html, dashboard_html):
    """A lapsed session shows up as HTTP 500, not a 401 or a login redirect."""
    responses.add(responses.POST, PROXY_URL, status=500, body="server error")
    responses.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    responses.add(
        responses.POST,
        LOGIN_URL,
        status=302,
        headers={"Location": DASHBOARD_URL},
        body="",
    )
    responses.add(responses.GET, DASHBOARD_URL, body=dashboard_html, status=200)
    responses.add(responses.POST, PROXY_URL, json={"d": '{"ok": true}'}, status=200)

    assert client._get("api/v1/anything") == {"ok": True}
    # It re-authenticated rather than surfacing the 500 as a fatal error.
    assert client.student_id == STUDENT_ID


@responses.activate
def test_expired_session_with_bad_password_raises_auth_error(client, login_html):
    """After a password change the retry must fail as auth, not as a server fault.

    Getting this wrong leaves the integration retrying forever instead of asking
    for the new password.
    """
    responses.add(responses.POST, PROXY_URL, status=500, body="server error")
    responses.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    responses.add(responses.POST, LOGIN_URL, body=login_html, status=200)
    with pytest.raises(MCASAuthError):
        client._get("api/v1/anything")


@responses.activate
def test_persistent_500_after_relogin_is_a_server_error(
    client, login_html, dashboard_html
):
    """A genuine server fault should not be misreported as bad credentials."""
    responses.add(responses.POST, PROXY_URL, status=500, body="server error")
    responses.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    responses.add(
        responses.POST,
        LOGIN_URL,
        status=302,
        headers={"Location": DASHBOARD_URL},
        body="",
    )
    responses.add(responses.GET, DASHBOARD_URL, body=dashboard_html, status=200)
    responses.add(responses.POST, PROXY_URL, status=500, body="server error")

    with pytest.raises(MCASError):
        client._get("api/v1/anything")


@responses.activate
def test_timetable_detects_a_bounce_to_the_login_page(client):
    # A lapsed session 302s the page request back to the login form.
    client.contact_id = CONTACT_ID
    responses.add(
        responses.GET,
        TIMETABLE_URL,
        status=302,
        headers={"Location": LOGIN_URL},
        body="",
    )
    responses.add(responses.GET, LOGIN_URL, body="<html>login</html>", status=200)
    with pytest.raises(MCASAuthError):
        client.timetable(STUDENT)


@responses.activate
def test_login_without_a_student_id_fails(client, login_html):
    """A None student id would otherwise end up in every API path."""
    responses.add(responses.GET, LOGIN_URL, body=login_html, status=200)
    responses.add(
        responses.POST,
        LOGIN_URL,
        status=302,
        headers={"Location": DASHBOARD_URL},
        body="",
    )
    responses.add(responses.GET, DASHBOARD_URL, body="<html>no vars</html>", status=200)
    with pytest.raises(MCASError):
        client.login()


@responses.activate
def test_students_lists_every_child(client, user_details):
    responses.add(
        responses.POST, PROXY_URL, json={"d": json.dumps(user_details)}, status=200
    )
    students = client.students()
    assert [s.id for s in students] == [STUDENT_ID, SECOND_STUDENT_ID]
    # Preferred names win; legal names fill in when no preferred name is set.
    assert students[0].name == "Alex Smith"
    assert students[1].name == "Sam Smith"
    assert students[0].school_id == SCHOOL_ID
    assert students[0].school_name == "Example High School"


@responses.activate
@pytest.mark.parametrize("payload", ["", json.dumps({"Students": []})])
def test_students_falls_back_to_the_dashboard_pupil(client, payload):
    """A single-child account must keep working if user/details gives nothing."""
    client.student_id, client.student_name = STUDENT_ID, "Alex Smith"
    client.school_id = SCHOOL_ID
    responses.add(responses.POST, PROXY_URL, json={"d": payload}, status=200)
    assert client.students() == [
        Student(id=STUDENT_ID, name="Alex Smith", school_id=SCHOOL_ID)
    ]


@responses.activate
def test_endpoints_use_the_student_id_passed_in(client):
    """The per-child endpoints are keyed by the path, not the session's child."""
    responses.add(responses.POST, PROXY_URL, json={"d": ""}, status=200)
    client.detentions(SECOND_STUDENT_ID)
    sent = json.loads(responses.calls[0].request.body)
    assert sent["url"].endswith(f"/{SECOND_STUDENT_ID}")


@responses.activate
def test_timetable_names_the_child(client, timetable_html):
    """Without the ids the page renders whichever child the session has selected."""
    client.contact_id = CONTACT_ID
    responses.add(
        responses.GET,
        TIMETABLE_URL,
        body=timetable_html,
        status=200,
        match=[
            matchers.query_param_matcher(
                {
                    "SchoolID": str(SCHOOL_ID),
                    "StudentID": str(SECOND_STUDENT_ID),
                    "ContactID": str(CONTACT_ID),
                }
            )
        ],
    )
    sam = Student(id=SECOND_STUDENT_ID, name="Sam Smith", school_id=SCHOOL_ID)
    assert client.timetable(sam)


@responses.activate
def test_timetable_without_a_contact_id_is_skipped(client):
    """Better no timetable than another child's."""
    assert client.timetable(STUDENT) == []
    assert not responses.calls
