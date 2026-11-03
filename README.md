# MyChildAtSchool (MCAS) for Home Assistant

[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![lint](https://github.com/robbrad/ha-mychildatschool-mcas/actions/workflows/lint.yml/badge.svg)](https://github.com/robbrad/ha-mychildatschool-mcas/actions/workflows/lint.yml)
[![tests](https://github.com/robbrad/ha-mychildatschool-mcas/actions/workflows/tests.yml/badge.svg)](https://github.com/robbrad/ha-mychildatschool-mcas/actions/workflows/tests.yml)

Home Assistant integration for the [MyChildAtSchool](https://www.mychildatschool.com)
parent portal (Bromcom MCAS), exposing your child's attendance, behaviour,
detentions and dinner balance as sensors.

## Sensors

| Sensor | Description |
| --- | --- |
| `Attendance today` | `Present` / `Not present` / `No data`, with each period's mark as attributes |
| `Attendance this week` | Percentage of recorded school days present in the last 7 days |
| `Behaviour events` | Count of behaviour events in the last 7 days, with the events (including the behaviour level, e.g. "Lesson Mark 1: Above Expected Attitude and Behaviour") as attributes |
| `Behaviour points` | Behaviour points for the academic year, with positive/negative splits, all-time totals, a per-subject breakdown and the most recent events |
| `Detentions` | Outstanding detentions |
| `Dinner balance` | Dinner money credit balance (GBP) |
| `School day` | Whether today is a `School day`, `Weekend`, `Holiday` or `Staff day`, per the school's own calendar, plus the next school day |
| `Lessons today` | Number of timetabled lessons today, with subject, class and teacher as attributes |
| `Next lesson` | Subject of the next timetabled lesson, with day, period, class and teacher |
| `Reports` | School reports available to view |
| `Clubs and trips` | Clubs and trips the pupil is enrolled on |

## Installation

Add this repository as a custom repository in HACS, install, restart Home
Assistant, then add **MyChildAtSchool (MCAS)** from
*Settings → Devices & Services* and sign in with your parent portal account.

## Polling

By default the portal is checked every 30 minutes between 07:00 and 18:00, on
school days only. School days come from the school's own calendar in MCAS, so
weekends, holidays and staff days are skipped. Dates the calendar doesn't cover
yet fall back to Monday to Friday. To change the interval or the times, open the
integration's ⋮ menu and choose **Configure**.

Outside those hours the sensors keep their last values. At midnight the "today"
sensors (Attendance today, Lessons today, Next lesson, School day) move on to
the new day using data already fetched, without calling the portal. Updating
any of the integration's sensors with `homeassistant.update_entity` still polls
straight away, at any time.

## How it works

MCAS is an ASP.NET WebForms site, but its pages talk to a real `api/v1` REST
backend through a single generic proxy web service. This integration logs in with
the normal parent credentials and then calls that backend directly, which is far
more robust than scraping the rendered pages.

Attendance is served one day at a time, so past school days are cached for the
life of the config entry and only today and yesterday are re-fetched. That keeps
the load on the school's portal to a few requests per poll. Behaviour events are
only available as an HTML fragment and are parsed with BeautifulSoup.

## Notes

- One MCAS login covers every child on the account. Each child gets its own device
  with the full set of sensors, all served over a single login. A child added to the
  account later appears when the integration is reloaded. If your children are at
  different schools, sensors are created from the module settings of the school the
  portal shows by default.
- **Sensors are only created for modules your school has enabled.** Schools license
  MCAS modules individually, and the integration reads that configuration at setup.
  A sensor that could only ever read zero is worse than no sensor, because it looks
  authoritative - a school running catering through a different provider would
  otherwise show a permanent £0.00 dinner balance.
- `Attendance today` reads `No data` at weekends, during holidays and before
  morning registration. That is normal, not a failure - pair it with `School day`.

## Disclaimer

Not affiliated with or endorsed by Bromcom. Uses the same endpoints as the parent
portal, with your own credentials.
