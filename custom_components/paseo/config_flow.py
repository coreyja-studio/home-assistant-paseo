"""Config flow for Paseo."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import async_probe_paseo
from .const import CONF_PASSWORD, CONF_URL, DEFAULT_URL, DOMAIN
from .models import PaseoAuthenticationError, PaseoConnectionError, normalize_websocket_url


class PaseoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Paseo."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_websocket_url(user_input[CONF_URL])
                password = user_input.get(CONF_PASSWORD) or None
                server = await async_probe_paseo(
                    async_get_clientsession(self.hass), url, password
                )
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            except PaseoAuthenticationError:
                errors["base"] = "invalid_auth"
            except PaseoConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(server["server_id"])
                self._abort_if_unique_id_configured(updates={CONF_URL: url})
                data = {CONF_URL: url}
                if password:
                    data[CONF_PASSWORD] = password
                return self.async_create_entry(title=server["hostname"], data=data)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_URL,
                    default=(user_input or {}).get(CONF_URL, DEFAULT_URL),
                ): str,
                vol.Optional(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
