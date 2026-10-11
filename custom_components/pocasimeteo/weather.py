"""Platforma pro hlavní weather entitu integrace PočasíMeteo."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.weather import WeatherEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DATABASE_DIAGNOSTICS,
    DOMAIN,
    SENSOR_DEFINITIONS,
    DEFAULT_SENSOR_OPTIONS,
    CONF_SENSORS,
    CONF_STATION,
)
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Nastavení weather platformy na základě konfigurace."""
    data_source = hass.data[DOMAIN][entry.entry_id]
    coordinator = (
        data_source if not isinstance(data_source, dict) else data_source.get("coordinator")
    )

    if coordinator is None:
        _LOGGER.error("Koordinátor nebyl v hass.data nalezen při zavádění weather entity")
        return

    async_add_entities([PocasimeteoWeather(coordinator, entry)])


class PocasimeteoWeather(
    CoordinatorEntity[PocasimeteoDataUpdateCoordinator], WeatherEntity
):
    """Hlavní stavová entita počasí meteostanice PočasíMeteo."""

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator, entry):
        """Inicializace entity s přímým předáním config entry."""
        super().__init__(coordinator)
        self._entry = entry

        station_prefix = (
            entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        )

        self._attr_unique_id = f"{entry.entry_id}_weather"
        self.entity_id = f"weather.{station_prefix}"
        self._attr_name = entry.title

        # ---------------------------------------------------------
        # DŮLEŽITÉ: WeatherEntity musí mít jednotky definované při
        # inicializaci, jinak Recorder uloží první stav s None.
        # ---------------------------------------------------------
        self._attr_native_temperature_unit = "°C"
        self._attr_native_pressure_unit = "hPa"
        self._attr_native_wind_speed_unit = "km/h"
        self._attr_native_wind_gust_unit = "km/h"
        self._attr_native_precipitation_unit = "mm"      # celkové srážky
        self._attr_native_precipitation_intensity_unit = "mm/h"  # intenzita srážek

        self._attr_supported_features = 0
        self._attr_device_info = coordinator.station_metadata.get("device_info")

    # -------------------------------------------------------------------------
    # HLAVNÍ HODNOTY WEATHER ENTITY
    # -------------------------------------------------------------------------

    @property
    def state(self) -> str | None:
        """Vrací aktuální stav počasí."""
        return self.coordinator.station_metadata.get("condition", "cloudy")

    @property
    def native_temperature(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("teplota_vnejsi")
        return (
            float(sensor["value"])
            if sensor and sensor.get("value") is not None
            else None
        )

    @property
    def humidity(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("vlhkost_vnejsi")
        return (
            float(sensor["value"])
            if sensor and sensor.get("value") is not None
            else None
        )

    @property
    def native_pressure(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("tlak_relativni")
        return (
            float(sensor["value"])
            if sensor and sensor.get("value") is not None
            else None
        )

    @property
    def precipitation_intensity(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("srazky_intenzita")
        return (
            float(sensor["value"])
            if sensor and sensor.get("value") is not None
            else None
        )

    @property
    def native_wind_speed(self) -> float | None:
        """Rychlost větru v km/h (HA standard)."""
        sensor = self.coordinator.sensors_payload.get("vitr_rychlost")
        if sensor and sensor.get("value") is not None:
            return round(float(sensor["value"]) * 3.6, 1)
        return None

    @property
    def native_wind_gust(self) -> float | None:
        """Nárazy větru v km/h (HA standard)."""
        sensor = self.coordinator.sensors_payload.get("vitr_narazy")
        if sensor and sensor.get("value") is not None:
            return round(float(sensor["value"]) * 3.6, 1)
        return None

    @property
    def wind_bearing(self) -> int | None:
        sensor = self.coordinator.sensors_payload.get("vitr_smer")
        return (
            int(sensor["value"])
            if sensor and sensor.get("value") is not None
            else None
        )

    # -------------------------------------------------------------------------
    # EXTRA ATTRIBUTES PRO LOVELACE KARTU
    # -------------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station_prefix = (
            self._entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        )
        sensor_options = self._entry.options.get(CONF_SENSORS, {})
        stats_dict = self.coordinator.station_metadata.get("sensor_stats", {})

        sensors_meta = []
        for sensor_id, meta in SENSOR_DEFINITIONS.items():
            options = sensor_options.get(
                sensor_id, DEFAULT_SENSOR_OPTIONS.get(sensor_id, {})
            )
            if not options.get("visible", True):
                continue

            sensors_meta.append(
                {
                    "id": sensor_id,
                    "entity_id": f"sensor.{station_prefix}_{sensor_id}",
                    "type": meta.get("sensor_type", "secondary"),
                    "order": options.get("order", meta.get("order", 999)),
                    "visible": True,
                    "graph_color": options.get(
                        "color", meta.get("color", "#3b82f6")
                    ),
                    "graph_style": options.get("style", "smooth"),
                }
            )

        # Dynamické senzory už jsou v Coordinatoru vedené pod interním ID.
        for sensor_id in self.coordinator.sensors_payload:
            if sensor_id in SENSOR_DEFINITIONS or sensor_id == "weather":
                continue
            sensors_meta.append(
                {
                    "id": sensor_id,
                    "entity_id": f"sensor.{station_prefix}_{sensor_id}",
                    "type": "secondary",
                    "order": 999,
                    "visible": True,
                    "graph_color": "#3b82f6",
                    "graph_style": "smooth",
                }
            )

        sensors_meta.sort(key=lambda sensor: sensor["order"])

        return {
            "srazky_den": self.coordinator.station_metadata.get("srazky_den", 0),
            "lokalita_stanice": self.coordinator.station_metadata.get(
                "lokalita_stanice"
            ),
            "timestamp": self.coordinator.station_metadata.get("api_timestamp"),
            "sensors": sensors_meta,
            "sensor_stats": stats_dict,
            # Zachovává se kvůli současné kartě; karta jej používá pro období
            # Recorder dotazu až do jejího přepnutí na get_history.
            "statistics_interval": self.coordinator._statistics_interval,
            ATTR_DATABASE_DIAGNOSTICS: self.coordinator.station_metadata.get(
                ATTR_DATABASE_DIAGNOSTICS, {}
            ),
        }
