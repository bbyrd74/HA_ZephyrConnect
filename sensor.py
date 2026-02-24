"""Zephyr Connect usage time sensors — confirmed from AK9434BS discoverdevice response."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, KEY_USE_CHARCOAL_TIME, KEY_USE_FAN_TIME,
    KEY_USE_GREASE_TIME, KEY_USE_LIGHT_TIME,
)

SENSORS = [
    (KEY_USE_FAN_TIME,      "Fan Usage Time",             "mdi:fan-clock"),
    (KEY_USE_LIGHT_TIME,    "Light Usage Time",           "mdi:lightbulb-on"),
    (KEY_USE_GREASE_TIME,   "Grease Filter Usage Time",   "mdi:air-filter"),
    (KEY_USE_CHARCOAL_TIME, "Charcoal Filter Usage Time", "mdi:air-filter"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    d = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ZephyrSensor(d["coordinator"], d["thing_name"], d["model_name"], d["serial"], *s)
        for s in SENSORS
    ])


class ZephyrSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.HOURS

    def __init__(self, coordinator, thing_name, model_name, serial, key, name, icon):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
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
    def native_value(self) -> int | None:
        val = (self.coordinator.data or {}).get(self._key)
        return int(val) if val is not None else None
