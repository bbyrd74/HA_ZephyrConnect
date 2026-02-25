"""Zephyr Connect number entities — sliders for fan speed and light level.

These complement the fan/light entities and give the user a direct
0–maxSpeed / 0–maxLevel slider in the HA UI.
"""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_FAN, KEY_LIGHT
from .exceptions import ZephyrMQTTError, ZephyrStaleClientCredsError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    d = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ZephyrSpeedSlider(
            coordinator=d["coordinator"], mqtt=d["mqtt"],
            thing_name=d["thing_name"], max_speed=d["max_fan"],
            model_name=d["model_name"], serial=d["serial"],
        ),
        ZephyrLightSlider(
            coordinator=d["coordinator"], mqtt=d["mqtt"],
            thing_name=d["thing_name"], max_level=d["max_light"],
            model_name=d["model_name"], serial=d["serial"],
        ),
    ])


class ZephyrSpeedSlider(CoordinatorEntity, NumberEntity):
    """Fan speed as a 0–maxFanSpeed slider (AK9434BS: 0–6)."""

    _attr_has_entity_name = True
    _attr_name = "Fan Speed"
    _attr_icon = "mdi:fan"
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0

    def __init__(self, coordinator, mqtt, thing_name, max_speed, model_name, serial):
        super().__init__(coordinator)
        self._mqtt = mqtt
        self._attr_unique_id = f"{thing_name}_fan_speed"
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = float(max_speed)
        self._optimistic: int | None = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thing_name)},
            "name": f"Zephyr {model_name}",
            "manufacturer": "Zephyr",
            "model": model_name,
            "serial_number": serial,
        }

    def _handle_coordinator_update(self) -> None:
        self._optimistic = None
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        if self._optimistic is not None:
            return float(self._optimistic)
        return float((self.coordinator.data or {}).get(KEY_FAN, 0))

    async def async_set_native_value(self, value: float) -> None:
        speed = int(value)
        self._optimistic = speed
        self.async_write_ha_state()
        try:
            await self.hass.async_add_executor_job(
                self._mqtt.publish_command, KEY_FAN, speed
            )
        except (ZephyrStaleClientCredsError, ZephyrMQTTError) as exc:
            _LOGGER.error("Fan speed command failed: %s", exc)
            self._optimistic = None
            self.async_write_ha_state()


class ZephyrLightSlider(CoordinatorEntity, NumberEntity):
    """Light level as a 0–maxLightLevel slider (AK9434BS: 0–3)."""

    _attr_has_entity_name = True
    _attr_name = "Light Level"
    _attr_icon = "mdi:brightness-6"
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0

    def __init__(self, coordinator, mqtt, thing_name, max_level, model_name, serial):
        super().__init__(coordinator)
        self._mqtt = mqtt
        self._attr_unique_id = f"{thing_name}_light_level"
        self._attr_native_min_value = 0.0
        self._attr_native_max_value = float(max_level)
        self._optimistic: int | None = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thing_name)},
            "name": f"Zephyr {model_name}",
            "manufacturer": "Zephyr",
            "model": model_name,
            "serial_number": serial,
        }

    def _handle_coordinator_update(self) -> None:
        self._optimistic = None
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        if self._optimistic is not None:
            return float(self._optimistic)
        return float((self.coordinator.data or {}).get(KEY_LIGHT, 0))

    async def async_set_native_value(self, value: float) -> None:
        level = int(value)
        self._optimistic = level
        self.async_write_ha_state()
        try:
            await self.hass.async_add_executor_job(
                self._mqtt.publish_command, KEY_LIGHT, level
            )
        except (ZephyrStaleClientCredsError, ZephyrMQTTError) as exc:
            _LOGGER.error("Light level command failed: %s", exc)
            self._optimistic = None
            self.async_write_ha_state()