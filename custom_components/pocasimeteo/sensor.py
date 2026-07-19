"""Sensor entities for PočasíMeteo integration.

This module contains ONLY:
- entity creation based on config entry options,
- mapping coordinator data to Home Assistant sensor entities,
- exposing min/max/timestamp attributes (computed in coordinator),
- using centralized metadata from const.py.

All structural definitions (names, units, icons, API mapping)
are stored in const.py.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_DEFINITIONS
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Setup entry: create sensor entities based on options["sensors"]
# ---------------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo sensor entities."""
    coordinator: PocasimeteoDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors_opt = entry.options.get("sensors", [])
    entities: list[PocasimeteoSensor] = []

    for sensor_def in sensors_opt:
        sid = sensor_def["id"]
        s_type = sensor_def["type"]
        order = sensor_def["order"]

        meta = SENSOR_DEFINITIONS.get(sid)

        if meta is None:
            # Custom sensor (not defined in const.py)
            entities.append(
                PocasimeteoSensor(
                    coordinator=coordinator,
                    sensor_id=sid,
                    name=sid,
                    unit=None,
                    icon="mdi:help-circle",
                    sensor_type=s_type,
                    order=order,
                )
            )
            continue

        # Standard sensor
        entities.append(
            PocasimeteoSensor(
                coordinator=coordinator,
                sensor_id=sid,
                name=meta["name"],
                unit=meta["unit"],
                icon=meta["icon"],
                sensor_type=s_type,
                order=order,
            )
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Sensor Entity
# ---------------------------------------------------------------------------
class PocasimeteoSensor(SensorEntity):
    """Representation of a single PočasíMeteo sensor."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        sensor_id: str,
        name: str,
        unit: str | None,
        icon: str,
        sensor_type: str,
        order: int,
    ) -> None:
        self.coordinator = coordinator
        self._sensor_id = sensor_id
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._sensor_type = sensor_type
        self._order = order

        self._attr_unique_id = f"{coordinator.entry.entry_id}_{sensor_id}"

    # ------------------------------------------------------------------
    # Coordinator update hook
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Entity is available if coordinator has data for this sensor."""
        data = self.coordinator.data
        return data is not None and self._sensor_id in data

    @property
    def native_value(self) -> Any:
        """Return the sensor's main value."""
        data = self.coordinator.data
        if not data:
            return None

        payload = data.get(self._sensor_id)
        if not payload:
            return None

        return payload.get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes (min/max/timestamp)."""
        data = self.coordinator.data
        if not data:
            return {}

        payload = data.get(self._sensor_id)
        if not payload:
            return {}

        return payload.get("attributes", {})

    async def async_added_to_hass(self) -> None:
        """Register coordinator listener."""
        self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Remove coordinator listener."""
        self.coordinator.remove_listener(self.async_write_ha_state)
