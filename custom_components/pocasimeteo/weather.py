"""Weather entity for PočasíMeteo."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.weather import (
    WeatherEntity,
)
from homeassistant.const import (
    TEMP_CELSIUS,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfPrecipitationDepth,
    UnitOfIrradiance,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    DEFAULT_NAME,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up PočasíMeteo weather entity from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            PocasimeteoWeatherEntity(
                coordinator=coordinator,
                entry=entry,
            )
        ],
        update_before_add=True,
    )


class PocasimeteoWeatherEntity(CoordinatorEntity, WeatherEntity):
    """Representation of PočasíMeteo current weather."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._entry = entry

        station_name = coordinator.data.get("station_name") or entry.title or DEFAULT_NAME
        self._attr_name = station_name
        self._attr_unique_id = f"{entry.entry_id}_weather"

    @property
    def native_temperature(self) -> float | None:
        """Return the current temperature in °C."""
        return self.coordinator.data.get("temperature")

    @property
    def native_temperature_unit(self) -> str:
        """Return the unit of measurement for temperature."""
        return TEMP_CELSIUS

    @property
    def humidity(self) -> float | None:
        """Return the relative humidity in %."""
        return self.coordinator.data.get("humidity")

    @property
    def native_pressure(self) -> float | None:
        """Return the pressure in hPa."""
        return self.coordinator.data.get("pressure")

    @property
    def native_pressure_unit(self) -> str:
        """Return the unit of measurement for pressure."""
        return UnitOfPressure.HPA

    @property
    def native_wind_speed(self) -> float | None:
        """Return the wind speed in m/s."""
        return self.coordinator.data.get("wind_speed")

    @property
    def native_wind_speed_unit(self) -> str:
        """Return the unit of measurement for wind speed."""
        return UnitOfSpeed.METERS_PER_SECOND

    @property
    def wind_gust(self) -> float | None:
        """Return the wind gust speed in m/s."""
        return self.coordinator.data.get("wind_gust")

    @property
    def wind_bearing(self) -> float | None:
        """Return the wind bearing in degrees."""
        return self.coordinator.data.get("wind_bearing")

    @property
    def condition(self) -> str | None:
        """Return a weather condition.

        Nové API neposkytuje přímo stav počasí (ikonu/kód),
        takže zde zatím nic nevracíme.
        """
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data
        attrs: dict[str, Any] = {}

        if "rain_daily" in data:
            attrs["rain_daily"] = data.get("rain_daily")
            attrs["rain_daily_unit"] = UnitOfPrecipitationDepth.MILLIMETERS

        if "rain_intensity" in data:
            attrs["rain_intensity"] = data.get("rain_intensity")
            # mm / 5 min – jednotku necháme jako popisný atribut
            attrs["rain_intensity_unit"] = "mm/5min"

        if "solar_radiation" in data:
            attrs["solar_radiation"] = data.get("solar_radiation")
            attrs["solar_radiation_unit"] = UnitOfIrradiance.WATTS_PER_SQUARE_METER

        if "uv_index" in data:
            attrs["uv_index"] = data.get("uv_index")

        if "timestamp" in data:
            attrs["last_update"] = data.get("timestamp")

        # Přidej raw data pro debug / advanced použití
        raw = data.get("raw")
        if isinstance(raw, dict):
            attrs["raw"] = raw

        meta = data.get("meta")
        if isinstance(meta, dict):
            attrs["meta"] = meta

        return attrs
