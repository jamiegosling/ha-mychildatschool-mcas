"""The polling options: an interval between a start and end time."""

from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mychildatschool.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="parent@example.com",
        data={"email": "parent@example.com", "password": "hunter2"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_default_to_school_hours(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {
        "scan_interval": 30,
        "start_time": "07:00:00",
        "end_time": "18:00:00",
    }


async def test_end_must_be_after_start(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 30, "start_time": "18:00:00", "end_time": "07:00:00"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "end_before_start"}


async def test_valid_options_are_saved(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Saving reloads the entry; keep that away from the real portal.
    with patch(
        "custom_components.mychildatschool.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"scan_interval": 15.0, "start_time": "08:00:00", "end_time": "16:00:00"},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "scan_interval": 15,
        "start_time": "08:00:00",
        "end_time": "16:00:00",
    }
