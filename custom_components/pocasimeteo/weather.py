"""Weather entity for PočasíMeteo integration."""

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
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfPrecipitationDepth,
)

from .const import DOMAIN, CONF_STATION
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo weather entity."""
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    station_name = entry.data.get(CONF_STATION, "Meteostanice")

    async_add_entities([PocasimeteoWeather(coordinator, station_name)])


class PocasimeteoWeather(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], WeatherEntity):
    """Representation of PočasíMeteo weather summary using CoordinatorEntity."""

    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature(0)

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator, station_name: str) -> None:
        """Initialize the weather entity."""
        super().__init__(coordinator)
        self._attr_name = station_name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_weather"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=station_name,
            manufacturer="PočasíMeteo.cz",
            model="Meteostanice",
        )

    @property
    def condition(self) -> str | None:
        """Return the current condition based on rain."""
        srazky = self._get_value("rainintensity")
        if srazky is not None and srazky > 0:
            return "rainy"
        return "sunny"

    @property
    def native_temperature(self) -> float | None:
        return self._get_value("teplotavnejsi")

    @property
    def native_pressure(self) -> float | None:
        return self._get_value("tlakrel")

    @property
    def humidity(self) -> float | None:
        return self._get_value("vlhkostvnejsi")

    @property
    def native_wind_speed(self) -> float | None:
        return self._get_value("vitr")

    @property
    def wind_bearing(self) -> float | str | None:
        return self._get_value("vitrsmer")

    @property
    def native_precipitation(self) -> float | None:
        return self._get_value("rainintensity")

    def _get_value(self, sid: str) -> Any:
        """Helper to get a value from coordinator data safely."""
        if not self.coordinator.data or sid not in self.coordinator.data:
            return None
        return self.coordinator.data[sid].get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose combined attributes and structural metadata for frontend card."""
        attrs: dict[str, Any] = {}
        sensors_metadata: list[dict[str, Any]] = []

        if not self.coordinator.data:
            return attrs

        # Procházíme všechny aktivní senzory z koordinátoru
        for sid, payload in self.coordinator.data.items():
            # Hodnoty pro starší verzi karty
            attrs[f"{sid}_value"] = payload.get("value")

            for key, val in payload.get("attributes", {}).items():
                attrs[f"{sid}_{key}"] = val

            # Struktura pro dynamické řazení grafů ve frontendu
            sensors_metadata.append({
                "id": sid,
                "type": payload.get("type", "secondary"),
                "order": payload.get("order", 200)
            })

        # Seřadíme senzory podle pořadí (order) z const.py
        sensors_metadata.sort(key=lambda x: x["order"])
        
        # Předáme kartě kompletní seřazenou strukturu
        attrs["sensors"] = sensors_metadata

        return attrs
