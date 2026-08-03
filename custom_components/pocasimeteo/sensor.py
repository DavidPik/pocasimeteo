"""Sensor platform for PočasíMeteo."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, SENSOR_DEFINITIONS
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo sensors from config entry."""
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    entities: list[PočasíMeteoSensor] = []

    for sensor_id, payload in coordinator.sensors_payload.items():
        meta = payload["meta"]
        entities.append(PočasíMeteoSensor(coordinator, entry, sensor_id, meta))

    async_add_entities(entities)


class PočasíMeteoSensor(SensorEntity):
    """Reprezentace jednoho senzoru PočasíMeteo."""

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        entry: ConfigEntry,
        sensor_id: str,
        meta: dict,
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        self._sensor_id = sensor_id
        self._meta = meta

        self._attr_unique_id = f"{entry.entry_id}_{sensor_id}"
        self._attr_name = meta.get("name", sensor_id)
        self._attr_icon = meta.get("icon")
        self._attr_native_unit_of_measurement = meta.get("unit")
        self._attr_device_class = meta.get("device_class")
        self._attr_state_class = meta.get("state_class")

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._coordinator.station_metadata.get("station_name") or "PočasíMeteo",
            manufacturer="PočasíMeteo",
            model="Meteostanice",
        )

    @property
    def native_value(self):
        payload = self._coordinator.sensors_payload.get(self._sensor_id)
        if not payload:
            return None
        return payload.get("value")
        
    @property
    def extra_state_attributes(self):
        payload = self._coordinator.sensors_payload.get(self._sensor_id)
        if not payload:
            return {}

        meta = payload["meta"]
        attrs = payload.get("attributes", {})

        return {
            "graph_color": meta.get("color"),
            "graph_style": meta.get("style"),
            "order": meta.get("order"),
            "visible": meta.get("visible"),
            **attrs,
        }

    async def async_update(self) -> None:
        await self._coordinator.async_request_refresh()
