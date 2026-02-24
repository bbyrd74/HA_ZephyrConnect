"""Zephyr Connect Home Assistant integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZephyrAuth, ZephyrMQTTClient, ZephyrRestClient
from .const import (
    AUTH_ERROR_STALE_CLIENT_CREDS,
    CONF_MAC,
    CONF_MAX_FAN_SPEED,
    CONF_MAX_LIGHT_LEVEL,
    CONF_MODEL_NAME,
    CONF_SERIAL,
    CONF_THING_NAME,
    DEFAULT_MAX_FAN_SPEED,
    DEFAULT_MAX_LIGHT_LEVEL,
    DOMAIN,
    SCAN_INTERVAL,
)
from .exceptions import (
    ZephyrAuthError,
    ZephyrMQTTError,
    ZephyrStaleClientCredsError,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.FAN, Platform.LIGHT, Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zephyr Connect from a config entry."""
    username   = entry.data[CONF_USERNAME]
    password   = entry.data[CONF_PASSWORD]
    thing_name = entry.data[CONF_THING_NAME]
    max_fan    = entry.data.get(CONF_MAX_FAN_SPEED, DEFAULT_MAX_FAN_SPEED)
    max_light  = entry.data.get(CONF_MAX_LIGHT_LEVEL, DEFAULT_MAX_LIGHT_LEVEL)
    model_name = entry.data.get(CONF_MODEL_NAME, "Range Hood")
    serial     = entry.data.get(CONF_SERIAL, "")
    mac        = entry.data.get(CONF_MAC, "")

    # ── Authenticate ──────────────────────────────────────────────────────────
    zauth = ZephyrAuth(username, password)
    try:
        await hass.async_add_executor_job(zauth.authenticate)
    except ZephyrStaleClientCredsError as exc:
        # The hardcoded app client credentials are no longer valid.
        # Raise ConfigEntryAuthFailed so HA shows a persistent repair notification.
        # The error message is shown to the user in the UI.
        raise ConfigEntryAuthFailed(
            f"Zephyr app credentials need updating. "
            f"The integration was built against APK v{entry.data.get('apk_version', '?')}. "
            f"A newer version may have rotated the credentials. "
            f"Please file an issue at the integration repo. ({exc})"
        ) from exc
    except ZephyrAuthError as exc:
        # Wrong password or user not found — user can fix via re-auth flow
        raise ConfigEntryAuthFailed(str(exc)) from exc

    # ── REST: refresh device info to get current maxFanSpeed/maxLightLevel ───
    rest = ZephyrRestClient(zauth)
    try:
        device_info = await hass.async_add_executor_job(rest.discover_device, thing_name)
        # Update max speeds in case Zephyr pushed a device firmware update that changed limits
        max_fan   = device_info.get("maxFanSpeed", max_fan)
        max_light = device_info.get("maxLightLevel", max_light)
    except Exception as exc:
        _LOGGER.warning("discoverdevice failed on setup, using stored values: %s", exc)
        device_info = {}

    # ── MQTT ──────────────────────────────────────────────────────────────────
    mqtt_client = ZephyrMQTTClient(zauth, thing_name)
    try:
        await hass.async_add_executor_job(mqtt_client.connect)
    except ZephyrMQTTError as exc:
        raise ConfigEntryNotReady(f"MQTT connection failed: {exc}") from exc

    # Seed state from REST so entities have values immediately
    if device_info:
        mqtt_client.seed_state(device_info)

    # ── Coordinator ───────────────────────────────────────────────────────────
    coordinator = ZephyrCoordinator(hass, mqtt_client, zauth, thing_name)
    mqtt_client.add_state_callback(coordinator.handle_mqtt_update)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "auth":        zauth,
        "rest":        rest,
        "mqtt":        mqtt_client,
        "coordinator": coordinator,
        "thing_name":  thing_name,
        "max_fan":     max_fan,
        "max_light":   max_light,
        "model_name":  model_name,
        "serial":      serial,
        "mac":         mac,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(data["mqtt"].disconnect)
    return unload_ok


class ZephyrCoordinator(DataUpdateCoordinator):
    """Manages device state — driven by MQTT push, polls as fallback.

    Also handles token refresh and stale credential detection during
    the lifetime of the integration (not just at startup).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        mqtt_client: ZephyrMQTTClient,
        zauth: ZephyrAuth,
        thing_name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{thing_name}",
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self._mqtt = mqtt_client
        self._auth = zauth

    async def _async_update_data(self) -> dict:
        """Fallback poll — returns cached MQTT state.

        Also checks for stale credentials on each poll cycle so we catch
        credential expiry before a user tries to issue a command.
        """
        try:
            await self.hass.async_add_executor_job(self._auth.ensure_valid)
        except ZephyrStaleClientCredsError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except ZephyrAuthError as exc:
            raise UpdateFailed(f"Auth error during poll: {exc}") from exc

        state = self._mqtt.get_state()
        if not state:
            raise UpdateFailed("No device state available yet")
        return state

    def handle_mqtt_update(self, state: dict) -> None:
        """Called by MQTT client on every shadow update — immediately pushes to HA."""
        self.async_set_updated_data(state)
