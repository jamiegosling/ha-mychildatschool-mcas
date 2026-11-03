"""The MyChildAtSchool (MCAS) integration."""

from __future__ import annotations

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .api import MCASClient
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import MCASCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MyChildAtSchool from a config entry."""
    session = await hass.async_add_executor_job(requests.Session)
    session.headers.update({"User-Agent": "HomeAssistant-MCAS"})
    client = MCASClient(session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = MCASCoordinator(hass, client, entry.options)
    await coordinator.async_config_entry_first_refresh()

    @callback
    def _new_day(_now) -> None:
        """Move "today" on at midnight from cached data, without polling.

        Nights are not polled, so without this the today sensors would show
        yesterday until the window opens.
        """
        if not coordinator.last_update_success or coordinator.data is None:
            return
        coordinator.schedule_next(dt_util.now())
        coordinator.async_set_updated_data(coordinator.summarise_all())

    entry.async_on_unload(
        async_track_time_change(hass, _new_day, hour=0, minute=0, second=5)
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
