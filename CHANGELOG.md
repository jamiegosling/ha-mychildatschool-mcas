# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## 0.2.0 (2026-11-03)

### Feat

- add configurable polling interval, start and stop time

## 0.1.3 (2026-10-02)

### Fix

- drop requests from the manifest requirements (#2)

## 0.1.2 (2026-09-14)

### Fix

- survive a transient login failure instead of demanding re-authentication

## 0.1.1 (2026-09-13)

### Fix

- recover from an expired session and offer re-authentication

## 0.1.0

Initial release.

- Attendance (today and a rolling week percentage), from the JSON `tableRawData`
  endpoint rather than the rendered table.
- Behaviour points for the academic year, with positive/negative splits, all-time
  totals and a per-subject breakdown, plus recent behaviour events including the
  level wording.
- Detentions.
- Timetable: lessons today and the next lesson, parsed from the portal's weekly
  grid (MCAS publishes no API for it).
- School day type - School day / Weekend / Holiday / Staff day - decoded from the
  school's own calendar, plus the next school day.
- Reports.
- Sensors are only created for modules the school has enabled, so a disabled
  module produces no permanently-zero entity and costs no API call.
