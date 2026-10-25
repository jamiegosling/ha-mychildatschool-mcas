"""Every child on the account gets its own device and sensors."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.mychildatschool import sensor
from custom_components.mychildatschool.api import Student
from custom_components.mychildatschool.const import DOMAIN


async def test_each_child_gets_its_own_device_and_data():
    coordinator = MagicMock()
    coordinator.modules = {"detentions": True}
    coordinator.students = [Student(id=1, name="A"), Student(id=2, name="B")]
    coordinator.data = {"students": {1: {"detentions": [{}, {}]}, 2: {}}}
    hass = SimpleNamespace(data={DOMAIN: {"entry": coordinator}})
    added = []

    await sensor.async_setup_entry(
        hass, SimpleNamespace(entry_id="entry"), added.extend
    )

    by_id = {entity.unique_id: entity for entity in added}
    # Keyed by student id, which is what single-child entries already used, so
    # existing entity ids survive the upgrade.
    assert by_id["1_detentions"].native_value == 2
    assert by_id["2_detentions"].native_value == 0
    assert {e.device_info["name"] for e in added} == {"A", "B"}
