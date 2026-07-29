"""Sensor entities for PočasíMeteo integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, SENSOR_DEFINITIONS, get_dynamic_sensor_meta, CONF_STATION
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo sensor entities dynamically based on API data."""
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    if not coordinator.data:
        return

    station_name = entry.data.get(CONF_STATION, "Meteostanice")
    entities: list[PocasimeteoSensor] = []

    for sid in coordinator.data.keys():
        meta = SENSOR_DEFINITIONS.get(sid)
        if meta is None:
            meta = get_dynamic_sensor_meta(sid)

        entities.append(
            PocasimeteoSensor(
                coordinator=coordinator,
                sensor_id=sid,
                meta=meta,
                station_name=station_name,
            )
        )

    async_add_entities(entities)


class PocasimeteoSensor(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], SensorEntity):
    """Representation of a PočasíMeteo sensor."""

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        sensor_id: str,
        meta: dict[str, Any],
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        self._meta = meta
        
        self._attr_name = f"{station_name} {meta['name']}"
        self._attr_icon = meta["icon"]
        self._attr_native_unit_of_measurement = meta["unit"] if meta["unit"] else None
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{sensor_id}"
        
        payload = coordinator.data.get(sensor_id, {}) if coordinator.data else {}
        if payload.get("is_numeric", True):
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_state_class = None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=station_name,
            manufacturer="PočasíMeteo.cz",
            model="Meteostanice",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor's current value."""
        if not self.coordinator.data or self._sensor_id not in self.coordinator.data:
            return None
        return self.coordinator.data[self._sensor_id].get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return min/max statistics, timestamp, graph color and specific wind stats."""
        attrs = {}
        attrs["graph_color"] = self._meta.get("color", "#7e57c2")
        
        if not self.coordinator.data or self._sensor_id not in self.coordinator.data:
            return attrs
            
        payload = self.coordinator.data[self._sensor_id]
        attrs.update(payload.get("attributes", {}))
        return attrs
