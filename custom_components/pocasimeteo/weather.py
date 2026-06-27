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
    def native_wind_gust_unit(self) -> str:
        return UnitOfSpeed.METERS_PER_SECOND

    @property
    def native_precipitation(self) -> float | None:
        return self.coordinator.data.get("SrazkyDen")

    @property
    def native_precipitation_unit(self) -> str:
        return UnitOfPrecipitationDepth.MILLIMETERS

    @property
    def wind_bearing(self) -> float | None:
        """Return wind direction in degrees."""
        return self.coordinator.data.get("VitrSmer")

    @property
    def condition(self) -> str | None:
        """Return weather condition (not provided by API)."""
        return None

    # ----------------------------------------------------------------------
    # ATTRIBUTES (including min/max)
    # ----------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        data = self.coordinator.data

        attrs = {
            "station_name": data.get("station_name"),
            "timestamp": data.get("timestamp"),
            "webcam_url": data.get("webcam_url"),

            # Current values
            "TeplotaVnejsi": data.get("TeplotaVnejsi"),
            "VlhkostVnejsi": data.get("VlhkostVnejsi"),
            "Vitr": data.get("Vitr"),
            "VitrNarazy": data.get("VitrNarazy"),
            "SrazkyDen": data.get("SrazkyDen"),
            "TlakRel": data.get("TlakRel"),
            "TeplotaVnitrni": data.get("TeplotaVnitrni"),
            "VlhkostVnitrni": data.get("VlhkostVnitrni"),
            "SlunZareni": data.get("SlunZareni"),
            "UVindex": data.get("UVindex"),
            "VitrSmer": data.get("VitrSmer"),

            # Wind direction + statistics
            "VitrSmer": data.get("VitrSmer"),
            "VitrSmer_mode": data.get("VitrSmer_mode"),
            "VitrSmer_avg": data.get("VitrSmer_avg"),
            "VitrSmer_var": data.get("VitrSmer_var"),
        }

        # Add min/max for all numeric keys
        for key in [
            "TeplotaVnejsi",
            "VlhkostVnejsi",
            "Vitr",
            "VitrNarazy",
            "SrazkyDen",
            "TlakRel",
            "TeplotaVnitrni",
            "VlhkostVnitrni",
            "SlunZareni",
            "UVindex",
        ]:
            attrs[f"{key}_min"] = data.get(f"{key}_min")
            attrs[f"{key}_max"] = data.get(f"{key}_max")

        return attrs

    # ----------------------------------------------------------------------
    # CONDITION (calculated from attributes) 
    # ----------------------------------------------------------------------

    @property
    def condition(self) -> str | None:
        data = self.coordinator.data

        slun = data.get("SlunZareni") or 0
        uv = data.get("UVindex") or 0
        vitr = data.get("Vitr") or 0

        # Night detection
        is_night = slun < 5 and uv < 0.1

        # Rain detection (compare last two measurements)
        measurements = self.coordinator.data.get("measurements")
        is_rain = False
        if measurements and len(measurements) >= 2:
            prev = measurements[-2].get("SrazkyDen") or 0
            curr = measurements[-1].get("SrazkyDen") or 0
            is_rain = curr > prev

        # Wind detection
        is_windy = vitr >= 8

        # Base condition
        if is_night:
            base = "night"
        elif is_rain:
            base = "rainy"
        elif slun > 400:
            base = "sunny"
        elif slun > 100:
            base = "partlycloudy"
        else:
            base = "cloudy"

        # Modifiers
        mods = []
        if is_windy:
            mods.append("windy")
        if is_rain and base != "rainy":
            mods.append("rainy")
        if is_night and base != "night":
            mods.append("night")

        if mods:
            return base + " & " + " & ".join(mods)
        return base

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
