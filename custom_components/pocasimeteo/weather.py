"""Weather entity for PočasíMeteo integration.

This module provides a lightweight Home Assistant weather entity
based on normalized data from the coordinator.

It exposes:
- temperature
- pressure
- humidity
- wind speed
- wind bearing
- precipitation intensity
- solar radiation
- UV index

All structural definitions (sensor metadata, API mapping)
are centralized in const.py.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_DEFINITIONS
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Setup entry: create weather entity
# ---------------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo weather entity."""
    coordinator: PocasimeteoDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PocasimeteoWeather(coordinator)])


# ---------------------------------------------------------------------------
# Weather Entity
# ---------------------------------------------------------------------------
class PocasimeteoWeather(WeatherEntity):
    """Representation of PočasíMeteo weather summary."""

    _attr_should_poll = False
    _attr_supported_features = WeatherEntityFeature.NONE

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_name = "PočasíMeteo"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_weather"

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    # ------------------------------------------------------------------
    # Temperature (°C)
    # ------------------------------------------------------------------
    @property
    def temperature(self) -> float | None:
        return self._get_value("TeplotaVnejsi")

    # ------------------------------------------------------------------
    # Pressure (hPa)
    # ------------------------------------------------------------------
    @property
    def pressure(self) -> float | None:
        return self._get_value("TlakRel")

    # ------------------------------------------------------------------
    # Humidity (%)
    # ------------------------------------------------------------------
    @property
    def humidity(self) -> float | None:
        return self._get_value("VlhkostVnejsi")

    # ------------------------------------------------------------------
    # Wind speed (m/s)
    # ------------------------------------------------------------------
    @property
    def wind_speed(self) -> float | None:
        return self._get_value("VitrRychlost")

    # ------------------------------------------------------------------
    # Wind bearing (°)
    # ------------------------------------------------------------------
    @property
    def wind_bearing(self) -> float | None:
        return self._get_value("VitrSmer")

    # ------------------------------------------------------------------
    # Precipitation intensity (mm/h)
    # ------------------------------------------------------------------
    @property
    def precipitation(self) -> float | None:
        return self._get_value("SrazkyIntenzita")

    # ------------------------------------------------------------------
    # Solar radiation (W/m²)
    # ------------------------------------------------------------------
    @property
    def solar_radiation(self) -> float | None:
        return self._get_value("SlunZareni")

    # ------------------------------------------------------------------
    # UV index
    # ------------------------------------------------------------------
    @property
    def uv_index(self) -> float | None:
        return self._get_value("UVIndex")

    # ------------------------------------------------------------------
    # Helper: read value from coordinator
    # ------------------------------------------------------------------
    def _get_value(self, sid: str) -> Any:
        data = self.coordinator.data
        if not data:
            return None

        payload = data.get(sid)
        if not payload:
            return None

        return payload.get("value")

    # ------------------------------------------------------------------
    # Attributes (timestamp, min/max)
    # ------------------------------------------------------------------
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose combined attributes from key sensors."""
        attrs: dict[str, Any] = {}

        for sid in [
            "TeplotaVnejsi",
            "VlhkostVnejsi",
            "TlakRel",
            "VitrRychlost",
            "VitrSmer",
            "SrazkyIntenzita",
            "SlunZareni",
            "UVIndex",
        ]:
            data = self.coordinator.data
            if not data or sid not in data:
                continue

            payload = data[sid]
            attrs[f"{sid}_value"] = payload.get("value")

            for key, val in payload.get("attributes", {}).items():
                attrs[f"{sid}_{key}"] = val

        return attrs

    # ------------------------------------------------------------------
    # Coordinator listener
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self.coordinator.async_remove_listener(self.async_write_ha_state)
