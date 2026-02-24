"""Config flow for Zephyr Connect.

Step 1 — user: collect email + password, attempt auth
  - Wrong password → show error inline
  - Stale client credentials → show specific error explaining the issue
  - Success → go to step 2

Step 2 — select_device: show discovered hoods, user picks one
  - Calls discoverdevice to get maxFanSpeed, maxLightLevel etc.
  - Creates config entry with all device metadata stored
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import ZephyrAuth, ZephyrRestClient
from .const import (
    COGNITO_APP_CLIENT_VERSION,
    CONF_MAC,
    CONF_MAX_FAN_SPEED,
    CONF_MAX_LIGHT_LEVEL,
    CONF_MODEL_NAME,
    CONF_SERIAL,
    CONF_THING_NAME,
    DEFAULT_MAX_FAN_SPEED,
    DEFAULT_MAX_LIGHT_LEVEL,
    DOMAIN,
)
from .exceptions import (
    ZephyrAuthError,
    ZephyrNetworkError,
    ZephyrStaleClientCredsError,
    ZephyrUserNotFoundError,
    ZephyrWrongPasswordError,
)

_LOGGER = logging.getLogger(__name__)


class ZephyrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Zephyr Connect."""

    VERSION = 1

    def __init__(self) -> None:
        self._auth: ZephyrAuth | None = None
        self._rest: ZephyrRestClient | None = None
        self._username = ""
        self._password = ""
        self._devices: list[dict] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1 — collect credentials and authenticate."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            zauth = ZephyrAuth(self._username, self._password)

            try:
                await self.hass.async_add_executor_job(zauth.authenticate)
            except ZephyrStaleClientCredsError:
                # The integration's hardcoded Cognito credentials are stale.
                # This is an integration-level problem, not a user error.
                errors["base"] = "stale_client_credentials"
                description_placeholders["apk_version"] = COGNITO_APP_CLIENT_VERSION
            except ZephyrWrongPasswordError:
                errors["base"] = "invalid_auth"
            except ZephyrUserNotFoundError:
                errors[CONF_USERNAME] = "user_not_found"
            except ZephyrNetworkError:
                errors["base"] = "cannot_connect"
            except ZephyrAuthError:
                errors["base"] = "unknown"
            else:
                # Auth succeeded — discover devices
                self._auth = zauth
                self._rest = ZephyrRestClient(zauth)
                try:
                    devices = await self.hass.async_add_executor_job(self._rest.get_devices)
                except Exception as exc:
                    _LOGGER.error("getowndevices failed: %s", exc)
                    errors["base"] = "cannot_connect"
                else:
                    if devices:
                        self._devices = devices
                        return await self.async_step_select_device()
                    errors["base"] = "no_devices"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_select_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2 — pick which hood to add."""
        errors: dict[str, str] = {}

        if user_input is not None:
            thing_name = user_input[CONF_THING_NAME]

            # Fetch full device capabilities from discoverdevice
            try:
                device_info = await self.hass.async_add_executor_job(
                    self._rest.discover_device, thing_name
                )
            except Exception as exc:
                _LOGGER.error("discoverdevice failed: %s", exc)
                # Fall back to safe defaults rather than blocking setup
                device_info = {}

            max_fan    = device_info.get("maxFanSpeed", DEFAULT_MAX_FAN_SPEED)
            max_light  = device_info.get("maxLightLevel", DEFAULT_MAX_LIGHT_LEVEL)
            model_name = device_info.get("modelName", "Range Hood")
            serial     = device_info.get("SN", "")
            mac        = device_info.get("MAC", "")

            _LOGGER.info(
                "Setting up %s (S/N %s, MAC %s) — maxFanSpeed=%s maxLightLevel=%s",
                model_name, serial, mac, max_fan, max_light,
            )

            await self.async_set_unique_id(thing_name)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=f"Zephyr {model_name} ({serial})",
                data={
                    CONF_USERNAME:        self._username,
                    CONF_PASSWORD:        self._password,
                    CONF_THING_NAME:      thing_name,
                    CONF_MAX_FAN_SPEED:   max_fan,
                    CONF_MAX_LIGHT_LEVEL: max_light,
                    CONF_MODEL_NAME:      model_name,
                    CONF_SERIAL:          serial,
                    CONF_MAC:             mac,
                    "apk_version":        COGNITO_APP_CLIENT_VERSION,
                },
            )

        # Build device picker — show model + S/N + MAC for each discovered hood
        options: dict[str, str] = {}
        for device in self._devices:
            tn = device.get("thingName", "")
            if not tn:
                continue
            model  = device.get("modelName", "Unknown")
            serial = device.get("SN", "?")
            mac    = device.get("MAC", "")
            options[tn] = f"{model}  —  S/N: {serial}  ({mac})"

        if not options:
            return self.async_abort(reason="no_devices")

        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({
                vol.Required(CONF_THING_NAME): vol.In(options),
            }),
            errors=errors,
        )
