"""Weather entity for PočasíMeteo with external forecast support."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
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
    """Set up PočasíMeteo weather entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            PocasimeteoWeatherEntity(
                hass=hass,
                coordinator=coordinator,
                entry=entry,
            )
        ],
        update_before_add=True,
    )


class PocasimeteoWeatherEntity(CoordinatorEntity, WeatherEntity):
    """Representation of PočasíMeteo current weather with external forecast."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY
        | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(self, hass: HomeAssistant, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._entry = entry

        station_name = coordinator.data.get("station_name") or entry.title or DEFAULT_NAME
        self._attr_name = station_name
        self._attr_unique_id = f"{entry.entry_id}_weather"

        # Forecast entity ID (optional)
        self._forecast_entity_id = entry.options.get("forecast_entity_id")

    # ----------------------------------------------------------------------
    # CURRENT CONDITIONS (from meteostation)
    # ----------------------------------------------------------------------

    @property
    def native_temperature(self) -> float | None:
        return self.coordinator.data.get("TeplotaVnejsi")

    @property
    def native_temperature_unit(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def humidity(self) -> float | None:
        return self.coordinator.data.get("VlhkostVnejsi")

    @property
    def native_pressure(self) -> float | None:
        return self.coordinator.data.get("TlakRel")

    @property
    def native_pressure_unit(self) -> str:
        return UnitOfPressure.HPA

    @property
    def native_wind_speed(self) -> float | None:
        return self.coordinator.data.get("Vitr")

    @property
    def native_wind_speed_unit(self) -> str:
        return UnitOfSpeed.METERS_PER_SECOND

    @property
    def native_wind_gust_speed(self) -> float | None:
        return self.coordinator.data.get("VitrNarazy")

    @property
    def wind_bearing(self) -> float | None:
        """Return wind bearing.

        Meteostanice neposkytuje směr větru jako číslo → necháme None.
        """
        return None

    @property
    def native_precipitation`(self) -> float | None:
        return self.coordinator.data.get("SrazkyDen")

    @property
    def condition(self) -> str | None:
        """Return weather condition.

        Meteostanice neposkytuje stav počasí → necháme None.
        """
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        data = self.coordinator.data

        return {
            "station_name": data.get("station_name"),
            "timestamp": data.get("timestamp"),
            "webcam_url": data.get("webcam_url"),

            "TeplotaVnejsi": data.get("TeplotaVnejsi"),
            "VlhkostVnejsi": data.get("VlhkostVnejsi"),
            "Vitr": data.get("Vitr"),
            "VitrSmer": data.get("VitrSmer"),
            "VitrNarazy": data.get("VitrNarazy"),
            "SrazkyDen": data.get("SrazkyDen"),
            "TlakRel": data.get("TlakRel"),

            "TeplotaVnitrni": data.get("TeplotaVnitrni"),
            "VlhkostVnitrni": data.get("VlhkostVnitrni"),

            "SlunZareni": data.get("SlunZareni"),
            "UVindex": data.get("UVindex"),

            "min_temp_24h": data.get("min_temp_24h"),
            "max_temp_24h": data.get("max_temp_24h"),
        }


    # ----------------------------------------------------------------------
    # FORECAST (from external weather entity)
    # ----------------------------------------------------------------------

    def _get_forecast_entity(self):
        if not self._forecast_entity_id:
            return None
        return self.hass.states.get(self._forecast_entity_id)

    async def async_forecast_daily(self):
        """Return daily forecast from external entity."""
        entity = self._get_forecast_entity()
        if not entity:
            return None

        forecast = entity.attributes.get("forecast_daily") or entity.attributes.get("forecast")
        return forecast

    async def async_forecast_hourly(self):
        """Return hourly forecast from external entity."""
        entity = self._get_forecast_entity()
        if not entity:
            return None

        forecast = entity.attributes.get("forecast_hourly")
        return forecast
