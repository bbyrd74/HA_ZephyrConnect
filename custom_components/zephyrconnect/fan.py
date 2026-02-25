"""Zephyr Connect fan entity.

Speed 0 = off, 1–maxFanSpeed = on (AK9434BS: max = 6).
HA percentage maps linearly to the device integer range.

Uses optimistic state updates — we write the new state locally immediately
on command so the UI stays in sync, rather than waiting for the shadow
echo which can carry stale values and cause the control to flap back.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_FAN
from .exceptions import ZephyrMQTTError, ZephyrStaleClientCredsError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    d = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZephyrFan(
        coordinator=d["coordinator"], mqtt=d["mqtt"],
        thing_name=d["thing_name"], max_speed=d["max_fan"],
        model_name=d["model_name"], serial=d["serial"],
    )])


class ZephyrFan(CoordinatorEntity, FanEntity):
    _attr_has_entity_name = True
    _attr_name = "Fan"
    # Explicitly declare all supported features — required in HA 2024+
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator, mqtt, thing_name, max_speed, model_name, serial):
        super().__init__(coordinator)
        self._mqtt = mqtt
        self._max_speed = max_speed
        self._attr_unique_id = f"{thing_name}_fan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thing_name)},
            "name": f"Zephyr {model_name}",
            "manufacturer": "Zephyr",
            "model": model_name,
            "serial_number": serial,
        }
        # Optimistic state — tracks what we commanded locally so the UI
        # does not flap while waiting for the device shadow echo
        self._optimistic_speed: int | None = None

    def _val(self) -> int:
        if self._optimistic_speed is not None:
            return self._optimistic_speed
        return (self.coordinator.data or {}).get(KEY_FAN, 0)

    @property
    def is_on(self) -> bool:
        return self._val() > 0

    @property
    def percentage(self) -> int:
        return round((self._val() / self._max_speed) * 100)

    @property
    def speed_count(self) -> int:
        return self._max_speed

    @property
    def extra_state_attributes(self) -> dict:
        return {"speed_level": self._val(), "max_speed": self._max_speed}

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state on real device update, then refresh UI."""
        self._optimistic_speed = None
        super()._handle_coordinator_update()

    async def _send(self, speed: int) -> None:
        # Reflect change in UI immediately
        self._optimistic_speed = speed
        self.async_write_ha_state()
        try:
            await self.hass.async_add_executor_job(
                self._mqtt.publish_command, KEY_FAN, speed
            )
        except (ZephyrStaleClientCredsError, ZephyrMQTTError) as exc:
            _LOGGER.error("Fan command failed: %s", exc)
            self._optimistic_speed = None
            self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        await self._send(round((percentage / 100) * self._max_speed))

    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any) -> None:
        speed = max(1, round((percentage / 100) * self._max_speed)) if percentage else 1
        await self._send(speed)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(0)