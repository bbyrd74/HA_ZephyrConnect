"""Zephyr Connect light entity.

Level 0 = off, 1–maxLightLevel = on (AK9434BS: max = 3).
HA brightness (0–255) maps to device level (0–maxLightLevel).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_LIGHT
from .exceptions import ZephyrMQTTError, ZephyrStaleClientCredsError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    d = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZephyrLight(
        coordinator=d["coordinator"], mqtt=d["mqtt"],
        thing_name=d["thing_name"], max_level=d["max_light"],
        model_name=d["model_name"], serial=d["serial"],
    )])


class ZephyrLight(CoordinatorEntity, LightEntity):
    _attr_has_entity_name = True
    _attr_name = "Light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, mqtt, thing_name, max_level, model_name, serial):
        super().__init__(coordinator)
        self._mqtt = mqtt
        self._max_level = max_level
        self._attr_unique_id = f"{thing_name}_light"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thing_name)},
            "name": f"Zephyr {model_name}",
            "manufacturer": "Zephyr",
            "model": model_name,
            "serial_number": serial,
        }

    def _val(self) -> int:
        return (self.coordinator.data or {}).get(KEY_LIGHT, 0)

    @property
    def is_on(self) -> bool:
        return self._val() > 0

    @property
    def brightness(self) -> int:
        return round((self._val() / self._max_level) * 255)

    @property
    def extra_state_attributes(self) -> dict:
        return {"light_level": self._val(), "max_level": self._max_level}

    async def _send(self, value: int) -> None:
        try:
            await self.hass.async_add_executor_job(self._mqtt.publish_command, KEY_LIGHT, value)
        except ZephyrStaleClientCredsError:
            _LOGGER.error("Cannot control light — Zephyr app credentials need updating")
        except ZephyrMQTTError as exc:
            _LOGGER.error("Light command failed: %s", exc)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = max(1, round((kwargs[ATTR_BRIGHTNESS] / 255) * self._max_level))
        else:
            current = self._val()
            level = current if current > 0 else self._max_level
        await self._send(level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(0)
