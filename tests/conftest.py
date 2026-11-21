"""Shared fixtures for the MyChildAtSchool tests.

Every value here is fabricated. Nothing in this repository should contain a real
pupil name, student id, school id or credential.
"""

import pytest

STUDENT_ID = 99999
SECOND_STUDENT_ID = 99998
SCHOOL_ID = 88888
YEAR_ID = 40000


@pytest.fixture
def login_html() -> str:
    """The login page, with the hidden WebForms fields the post must echo back."""
    return """
    <html><body><form method="post" action="./MCSParentLogin">
      <input type="hidden" name="__VIEWSTATE" value="abc123" />
      <input type="hidden" name="__VIEWSTATEGENERATOR" value="F50534A4" />
      <input type="hidden" name="__EVENTVALIDATION" value="xyz789" />
      <input name="EmailTextBox" type="text" />
      <input name="PasswordTextBox" type="password" />
    </form></body></html>
    """


@pytest.fixture
def dashboard_html() -> str:
    """The dashboard, which carries the pupil context as plain JS vars."""
    return f"""
    <html><body>
      <span id="ctl00_StudentNameLabel">Smith, Alex</span>
      <script>
        var master_studentid = {STUDENT_ID};
        var master_userid = 12345;
        var master_schoolID = "{SCHOOL_ID}";
        var master_schoolName = "Example High School";
      </script>
    </body></html>
    """


@pytest.fixture
def user_details() -> dict:
    """The account's user/details payload, trimmed to the fields that are read."""
    return {
        "Students": [
            {
                "ID": STUDENT_ID,
                "SchoolID": SCHOOL_ID,
                "SchoolName": "Example High School",
                "FirstName": "Alexander",
                "LastName": "Smith",
                "PreferredFirstName": "Alex",
                "PreferredLastName": "Smith",
            },
            {
                "ID": SECOND_STUDENT_ID,
                "SchoolID": SCHOOL_ID,
                "SchoolName": "Example High School",
                "FirstName": "Sam",
                "LastName": "Smith",
                "PreferredFirstName": "",
                "PreferredLastName": None,
            },
        ]
    }


@pytest.fixture
def behaviour_html() -> str:
    """A behaviour events fragment - MCAS returns HTML, not JSON, for this view."""
    return """
    <table class='table'><thead><tr>
      <th>Date</th><th>Class</th><th>Subject</th><th>Teacher</th><th>Comment</th><th>Event</th>
    </tr></thead><tbody>
      <tr><td>11/09/2026</td><td>7x/Mu1</td><td>Music</td><td>Mr Example</td>
          <td>Bulk event</td>
          <td>Lesson Mark 1: Above Expected Attitude and Behaviour</td></tr>
      <tr><td>11/09/2026</td><td>7ZZ</td><td>Tutor Period</td><td>Ms Example</td>
          <td>Bulk event</td>
          <td>Lesson Mark 2: Expected Attitude and Behaviour</td></tr>
    </tbody></table>
    """


@pytest.fixture
def timetable_html() -> str:
    """A week grid. Cell text is ellipsised; the `title` attributes are canonical."""
    return """
    <html><body><table>
      <tr><th>Monday14th Sep</th><th>Tuesday15th Sep</th></tr>
      <tr>
        <td><div>Tutor</div><div title="Example High School">Example Hig...</div>
            <div title="Tutor Period">Tutor Period</div><div title="7ZZ">7ZZ</div>
            <div title="Ms Example">Ms Example</div></td>
        <td><div>Tutor</div><div title="Example High School">Example Hig...</div>
            <div title="Tutor Period">Tutor Period</div><div title="7ZZ">7ZZ</div>
            <div title="Ms Example">Ms Example</div></td>
      </tr>
      <tr>
        <td><div>1</div><div title="Example High School">Example Hig...</div>
            <div title="Rel. Stud.">Rel. Stud.</div><div title="7y/Re1">7y/Re1</div>
            <div title="Miss Example">Miss Example</div></td>
        <td><div>1</div><div title="Example High School">Example Hig...</div>
            <div title="Drama">Drama</div><div title="7y/Dr1">7y/Dr1</div>
            <div title="Mr Example">Mr Example</div></td>
      </tr>
    </table></body></html>
    """
