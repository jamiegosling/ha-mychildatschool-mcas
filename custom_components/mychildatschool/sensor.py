"""Sensors for MyChildAtSchool."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Student
from .const import DOMAIN
from .coordinator import MCASCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the MCAS sensors."""
    coordinator: MCASCoordinator = hass.data[DOMAIN][entry.entry_id]
    modules = coordinator.modules or {}

    # Only create entities for modules the school has enabled. A sensor that can
    # only ever read zero is worse than no sensor: it looks authoritative.
    sensor_types: list[type[MCASEntity]] = [SchoolDaySensor]
    if modules.get("attendance", True):
        sensor_types += [AttendanceTodaySensor, AttendancePercentSensor]
    if modules.get("behaviour", True):
        sensor_types += [BehaviourEventsSensor, BehaviourPointsSensor]
    if modules.get("detentions", True):
        sensor_types.append(DetentionsSensor)
    if modules.get("timetable", True):
        sensor_types += [LessonsTodaySensor, NextLessonSensor]
    if modules.get("reports", True):
        sensor_types.append(ReportsSensor)
    if modules.get("clubs") or modules.get("trips"):
        sensor_types.append(ClubsAndTripsSensor)
    if modules.get("dinner"):
        sensor_types.append(DinnerBalanceSensor)

    # One device per child, each with the full set of sensors.
    async_add_entities(
        sensor_type(coordinator, student)
        for student in coordinator.students or []
        for sensor_type in sensor_types
    )


class MCASEntity(CoordinatorEntity[MCASCoordinator], SensorEntity):
    """Shared device and naming. One device per child."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MCASCoordinator, student: Student, key: str
    ) -> None:
        super().__init__(coordinator)
        self._student_id = student.id
        # Keyed by student id. Single-child entries made before multi-child
        # support used the student id as the entry's unique_id, so their entity
        # and device ids come out unchanged.
        self._attr_unique_id = f"{student.id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(student.id))},
            name=student.name or "MyChildAtSchool",
            manufacturer="Bromcom",
            model="MyChildAtSchool",
            configuration_url="https://www.mychildatschool.com/MCAS/MCSParentLogin",
        )

    @property
    def _data(self) -> dict:
        """This child's slice of the coordinator data."""
        return (
            (self.coordinator.data or {}).get("students", {}).get(self._student_id, {})
        )


class AttendanceTodaySensor(MCASEntity):
    """Whether the pupil was registered present today."""

    _attr_icon = "mdi:school"

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "attendance_today")
        self._attr_name = "Attendance today"

    @property
    def native_value(self) -> str:
        day = self._data.get("attendance_today")
        if day is None or not day.periods:
            # Weekends, holidays and mornings before registration all land here.
            return "No data"
        return "Present" if day.present else "Not present"

    @property
    def extra_state_attributes(self) -> dict:
        day = self._data.get("attendance_today")
        if day is None:
            return {}
        return {
            "summary": day.summary,
            "periods": [
                {
                    "period": p.get("PeriodName"),
                    "mark": p.get("MarkMeaning"),
                    "description": p.get("MarkDescription"),
                    "subject": p.get("SubjectName"),
                }
                for p in day.periods
            ],
        }


class AttendancePercentSensor(MCASEntity):
    """Percentage of recorded school days present over the lookback window."""

    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "attendance_percent")
        self._attr_name = "Attendance this week"

    @property
    def native_value(self):
        return self._data.get("attendance_pct")

    @property
    def extra_state_attributes(self) -> dict:
        days = self._data.get("attendance_days") or []
        return {
            "days_recorded": len(days),
            "days": {d.day.isoformat(): d.summary for d in days},
        }


class BehaviourEventsSensor(MCASEntity):
    """Count of behaviour events recorded in the lookback window."""

    _attr_icon = "mdi:account-alert"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "behaviour_events")
        self._attr_name = "Behaviour events"

    @property
    def native_value(self) -> int:
        return len(self._data.get("behaviour_events") or [])

    @property
    def extra_state_attributes(self) -> dict:
        events = self._data.get("behaviour_events") or []
        return {"events": events[:20]}


class BehaviourPointsSensor(MCASEntity):
    """Behaviour points for the academic year."""

    _attr_icon = "mdi:star-circle"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "points"

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "behaviour_points")
        self._attr_name = "Behaviour points"

    @property
    def native_value(self):
        return (self._data.get("behaviour_points") or {}).get("total")

    @property
    def extra_state_attributes(self) -> dict:
        points = dict(self._data.get("behaviour_points") or {})
        events = self._data.get("behaviour_year_events") or []
        by_subject: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for event in events:
            subject = event.get("subject") or "Unknown"
            by_subject[subject] = by_subject.get(subject, 0) + (
                event.get("points") or 0
            )
            kind = event.get("type") or "Unknown"
            by_type[kind] = by_type.get(kind, 0) + 1
        points.update(
            {
                "academic_year": self._data.get("behaviour_year_name"),
                "events_this_year": len(events),
                "points_by_subject": {k: v for k, v in sorted(by_subject.items()) if v},
                "events_by_type": by_type,
                "recent": events[:10],
            }
        )
        return points


class DetentionsSensor(MCASEntity):
    """Outstanding detentions."""

    _attr_icon = "mdi:clock-alert"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "detentions")
        self._attr_name = "Detentions"

    @property
    def native_value(self) -> int:
        return len(self._data.get("detentions") or [])

    @property
    def extra_state_attributes(self) -> dict:
        return {"detentions": self._data.get("detentions") or []}


class DinnerBalanceSensor(MCASEntity):
    """Dinner money credit balance."""

    _attr_icon = "mdi:food"
    _attr_native_unit_of_measurement = "GBP"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "dinner_balance")
        self._attr_name = "Dinner balance"

    @property
    def native_value(self):
        return self._data.get("dinner_balance")


class SchoolDaySensor(MCASEntity):
    """What kind of day today is, per the school's own calendar."""

    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "school_day")
        self._attr_name = "School day"

    @property
    def native_value(self):
        return self._data.get("day_type") or "Unknown"

    @property
    def extra_state_attributes(self) -> dict:
        return {"next_school_day": self._data.get("next_school_day")}


class LessonsTodaySensor(MCASEntity):
    """Lessons timetabled for today."""

    _attr_icon = "mdi:timetable"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "lessons_today")
        self._attr_name = "Lessons today"

    @property
    def native_value(self) -> int:
        return len(self._data.get("lessons_today") or [])

    @property
    def extra_state_attributes(self) -> dict:
        return {"lessons": self._data.get("lessons_today") or []}


class NextLessonSensor(MCASEntity):
    """First lesson of the next timetabled day."""

    _attr_icon = "mdi:chevron-right-circle"

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "next_lesson")
        self._attr_name = "Next lesson"

    @property
    def native_value(self):
        lesson = self._data.get("next_lesson")
        return lesson.get("subject") if lesson else None

    @property
    def extra_state_attributes(self) -> dict:
        return self._data.get("next_lesson") or {}


class ReportsSensor(MCASEntity):
    """School reports available to view."""

    _attr_icon = "mdi:file-document"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "reports")
        self._attr_name = "Reports"

    @property
    def native_value(self) -> int:
        return len(self._data.get("reports") or [])

    @property
    def extra_state_attributes(self) -> dict:
        return {"reports": self._data.get("reports") or []}


class ClubsAndTripsSensor(MCASEntity):
    """Clubs and trips the pupil is enrolled on."""

    _attr_icon = "mdi:bus"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, student):
        super().__init__(coordinator, student, "clubs_and_trips")
        self._attr_name = "Clubs and trips"

    @property
    def native_value(self) -> int:
        return len(self._data.get("clubs_and_trips") or [])

    @property
    def extra_state_attributes(self) -> dict:
        return {"items": self._data.get("clubs_and_trips") or []}
