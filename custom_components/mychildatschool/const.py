"""Constants for the MyChildAtSchool (MCAS) integration."""

from datetime import timedelta

DOMAIN = "mychildatschool"

BASE_URL = "https://www.mychildatschool.com"
LOGIN_PATH = "/MCAS/MCSParentLogin"
DASHBOARD_MARKER = "MCSDashboard"
# Generic server-side proxy onto the api/v1 REST backend. Every data call goes
# through this: {"url": "api/v1/...", ...} -> {"d": "<json string or html>"}.
PROXY_PATH = "/MCAS/WebServices/MCSAPIRequestProxy.asmx/CreateGetRequest"

# api/v1 routes. The full route map is a public file served by the portal at
# /Scripts/Blankon/Custom/apiEndPoints.js - consult it before inventing a path.
EP_USER_DETAILS = "api/v1/mcas/user/details"
EP_STUDENT_YEARS = "api/v1/mcas/homework/studentYears/{sid}"
# tableRawData is the JSON sibling of .../table/ (which returns an HTML fragment).
EP_ATTENDANCE = "api/v1/attendance/mcas/tableRawData/{sid}/{y}/{m}/{d}/-1"
EP_BEHAVIOUR = "api/v1/eventRecords/mcas/eventstable/{sid}/{yid}/{y}/{m}/{d}/-1"
# The whole academic year in one JSON call, and much richer than the per-day HTML:
#   Table  - every behaviour event (EventType, Adjustment = points, SubjectID)
#   Table1 - school calendar day status codes
#   Table2 - year name
#   Table3 - SubjectID -> SubjectName/colour lookup
#   Table4 - all-time points totals
EP_BEHAVIOUR_DETAIL = "api/v1/eventRecords/mcas/eventdetails/{sid}/{yid}"
EP_DETENTIONS = "api/v1/detentions/mcas/{sid}"
EP_DINNER = "api/v1/mcas/dashboard/GetDinnerBalanceWidgetData/{sid}"

EP_CONFIGURATIONS = "api/v1/mcas/configurations"
# Schools license MCAS modules individually. Creating sensors for modules the
# school has switched off produces entities that are permanently zero and read as
# authoritative when they are not. The dinner balance is the clearest case: a
# school that takes catering payments through a different provider still reports a
# confident 0.00 here, which is worse than having no sensor at all.
MODULE_FLAGS = {
    "attendance": "MCASAttendanceModuleEnabled",
    "behaviour": "MCASBehaviourModuleEnabled",
    "detentions": "MCASEnableDetentions",
    "timetable": "MCASTimetableModuleEnabled",
    "reports": "MCASReportsModuleEnabled",
    "dinner": "MCASDinnerMoneyModule_EnableDinnerMoneyModule",
    "clubs": "MCASClubsModuleEnabled",
    "trips": "MCASTripsModuleEnabled",
}

EP_REPORTS = "api/v1/studentDetails/reports/{sid}"
EP_CLUBS = "api/v1/mcas/clubsandtrips/StudentClubsAndTrips/{sid}"
# The timetable and academic calendar pages are server-rendered - no API call
# exists for them, so the week grid is parsed out of the page itself.
# Takes ?SchoolID=&StudentID=&ContactID= to pick the child; see MCASClient.timetable.
PAGE_TIMETABLE = "/MCAS/MCSTimetable"

# DayStatusCode values in the behaviour year payload's calendar table. Decoded by
# correlating a full year against weekdays: 187 "-" days is a normal UK school year,
# and the 6 "$" days match a school's INSET allocation.
SCHOOL_DAY = "School day"
DAY_STATUS = {"-": SCHOOL_DAY, "*": "Weekend", "#": "Holiday", "$": "Staff day"}

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

CONF_START_TIME = "start_time"
CONF_END_TIME = "end_time"

# Polling runs every DEFAULT_SCAN_INTERVAL between the start and end times, on
# school days only. Nothing changes overnight or in the holidays, and each poll
# costs up to ten requests per child, so polling round the clock mostly loaded the
# school's portal for nothing. All three are configurable in the options.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=30)
DEFAULT_START_TIME = "07:00:00"
DEFAULT_END_TIME = "18:00:00"
# Consecutive authentication failures tolerated before asking the user to sign in
# again. MCAS returns HTTP 200 and a rendered page for every outcome, so a blip is
# indistinguishable from a wrong password; demanding re-auth on the first failure
# left the integration dead until it was restarted by hand, when the very next
# poll would have recovered on its own.
AUTH_FAILURES_BEFORE_REAUTH = 3
# How many calendar days back to consider "this week" when summarising.
WEEK_LOOKBACK_DAYS = 7
# Marks that count as the pupil being in school.
PRESENT_MARK_SIGNS = {"P", "L"}
