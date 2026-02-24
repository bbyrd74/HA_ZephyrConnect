"""Zephyr Connect binary sensors — confirmed from AK9434BS discoverdevice response."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, KEY_ALARM_FAN, KEY_ALARM_GREASE, KEY_CLEAN_CHARCOAL,
    KEY_CLEAN_GREASE, KEY_FAN_WARNING, KEY_IS_ONLINE,
)

SENSORS = [
    (KEY_IS_ONLINE,      "Online",                BinarySensorDeviceClass.CONNECTIVITY, "mdi:cloud-check"),
    (KEY_CLEAN_GREASE,   "Grease Filter Alert",   BinarySensorDeviceClass.PROBLEM,      "mdi:air-filter"),
    (KEY_ALARM_GREASE,   "Grease Filter Alarm",   BinarySensorDeviceClass.PROBLEM,      "mdi:alert"),
    (KEY_CLEAN_CHARCOAL, "Charcoal Filter Alert", BinarySensorDeviceClass.PROBLEM,      "mdi:air-filter"),
    (KEY_FAN_WARNING,    "Fan Warning",            BinarySensorDeviceClass.PROBLEM,      "mdi:fan-alert"),
    (KEY_ALARM_FAN,      "Fan Alarm",              BinarySensorDeviceClass.PROBLEM,      "mdi:fan-alert"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    d = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ZephyrBinarySensor(d["coordinator"], d["thing_name"], d["model_name"], d["serial"], *s)
        for s in SENSORS
    ])


class ZephyrBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, thing_name, model_name, serial, key, name, device_class, icon):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_unique_id = f"{thing_name}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, thing_name)},
            "name": f"Zephyr {model_name}",
            "manufacturer": "Zephyr",
            "model": model_name,
            "serial_number": serial,
        }

    @property
    def is_on(self) -> bool | None:
        val = (self.coordinator.data or {}).get(self._key)
        return None if val is None else int(val) == 1
