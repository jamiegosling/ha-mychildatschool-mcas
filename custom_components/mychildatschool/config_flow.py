"""Config flow for MyChildAtSchool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: F401

from .api import MCASAuthError, MCASClient, MCASError, Student
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})


class MCASConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Ask for the parent portal login and verify it before creating the entry."""

    VERSION = 1

    def __init__(self) -> None:
        """Track the entry being re-authenticated, if any."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input=None):
        """Collect the portal login and verify it before creating the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            # Entries made before multi-child support are keyed by student id, so
            # matching on the unique_id alone would not catch them.
            if any(
                entry.data.get(CONF_EMAIL, "").strip().lower() == email
                for entry in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")
            try:
                students = await self.hass.async_add_executor_job(
                    self._students, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            except MCASAuthError:
                errors["base"] = "invalid_auth"
            except MCASError:
                errors["base"] = "cannot_connect"
            else:
                # One login covers every child on the account, so it is the login
                # that is unique.
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()
                names = [s.name for s in students if s.name]
                return self.async_create_entry(
                    title=", ".join(names) or "MyChildAtSchool", data=user_input
                )
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Start re-authentication when the stored password stops working.

        Without this the integration can only sit unavailable after a password
        change - the entry has to be deleted and re-added to fix it.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for the new password and verify it before storing."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if user_input is not None:
            email = user_input.get(CONF_EMAIL) or entry.data[CONF_EMAIL]
            try:
                await self.hass.async_add_executor_job(
                    self._verify, email, user_input[CONF_PASSWORD]
                )
            except MCASAuthError:
                errors["base"] = "invalid_auth"
            except MCASError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    def _verify(email: str, password: str) -> MCASClient:
        import requests

        session = requests.Session()
        session.headers.update({"User-Agent": "HomeAssistant-MCAS"})
        client = MCASClient(session, email, password)
        client.login()
        return client

    @classmethod
    def _students(cls, email: str, password: str) -> list[Student]:
        return cls._verify(email, password).students()
