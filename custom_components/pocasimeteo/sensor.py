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

from .const import (
    DOMAIN,
    SENSOR_DEFINITIONS,
    get_dynamic_sensor_meta,
    CONF_STATION,
)
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SETUP ENTRY
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Dynamically create sensor entities based on coordinator data.
    Each sensor is created only if coordinator provides a payload for it.
    """

    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    if not coordinator.data:
        return

    station_name = entry.data.get(CONF_STATION, "Meteostanice")
    entities: list[PocasimeteoSensor] = []

    # Create entities for all sensors present in coordinator.data
    for sid in coordinator.data.keys():
        meta = SENSOR_DEFINITIONS.get(sid, get_dynamic_sensor_meta(sid))

        entities.append(
            PocasimeteoSensor(
                coordinator=coordinator,
                sensor_id=sid,
                meta=meta,
                station_name=station_name,
            )
        )

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# SENSOR ENTITY
# ---------------------------------------------------------------------------

class PocasimeteoSensor(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], SensorEntity):
    """
    Representation of a single PočasíMeteo sensor.

    This entity:
    - Reads normalized data from the coordinator
    - Exposes numeric values and statistics (min/max)
    - Exposes graph metadata (color, style, order, visibility)
    - Uses DeviceInfo to group sensors under one device
    """

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        sensor_id: str,
        meta: dict[str, Any],
        station_name: str,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator)

        self._sensor_id = sensor_id
        self._meta = meta

        # Friendly name
        self._attr_name = f"{station_name} {meta['name']}"

        # Icon and unit
        self._attr_icon = meta["icon"]
        self._attr_native_unit_of_measurement = meta["unit"] or None

        # Unique ID
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{sensor_id}"

        # Determine state class (numeric vs non-numeric)
        payload = coordinator.data.get(sensor_id, {}) if coordinator.data else {}
        if payload.get("is_numeric", True):
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_state_class = None

        # Device info (grouping under one meteostation)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=station_name,
            manufacturer="PočasíMeteo.cz",
            model="Meteostanice",
        )

    # -----------------------------------------------------------------------
    # MAIN VALUE
    # -----------------------------------------------------------------------

    @property
    def native_value(self) -> Any:
        """Return the sensor's current value."""
        if not self.coordinator.data:
            return None
        payload = self.coordinator.data.get(self._sensor_id)
        if not payload:
            return None
        return payload.get("value")

    # -----------------------------------------------------------------------
    # EXTRA ATTRIBUTES
    # -----------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Return additional attributes:
        - timestamp
        - min/max statistics
        - wind direction statistics (avg/mode/variance)
        - graph metadata (color, style, order, visibility)
        """

        attrs: dict[str, Any] = {}

        # Graph metadata from coordinator (user-configured)
        payload = self.coordinator.data.get(self._sensor_id, {})

        attrs["graph_color"] = payload.get("graph_color", self._meta.get("color"))
        attrs["graph_style"] = payload.get("graph_style", "smooth")
        attrs["order"] = payload.get("order", self._meta.get("order"))
        attrs["visible"] = payload.get("visible", True)

        # Add statistics and timestamp
        attrs.update(payload.get("attributes", {}))

        return attrs
