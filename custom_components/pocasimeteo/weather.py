"""Weather platform for PočasíMeteo."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.weather import WeatherEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_STATION,
    ATTR_STATION_LOCATION,
    ATTR_API_TIMESTAMP,
    ATTR_DAILY_RAIN,
    CONF_SENSORS,
    CONF_STATISTICS_INTERVAL,
    SENSOR_DEFINITIONS,
)
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

    async_add_entities([PocasimeteoWeather(coordinator, entry)])


class PocasimeteoWeather(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], WeatherEntity):
    """Hlavní weather entita pro PočasíMeteo provázaná s koordinátorem."""

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Inicializace weather entity a provázání se zařízením."""
        super().__init__(coordinator)
        self._entry = entry

        self._attr_unique_id = entry.entry_id
        self._attr_name = self._entry.data.get(CONF_STATION) or "PočasíMeteo"

        self._attr_supported_features = 0

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._entry.data.get(CONF_STATION) or "PočasíMeteo",
            manufacturer="PočasíMeteo",
            model="Meteostanice",
        )

    #
    # === STANDARD HA WEATHERENTITY API ===
    #

    @property
    def condition(self) -> str | None:
        """Odvozený stav počasí vypočtený z aktuálních hodnot senzorů."""
        sensors = self.coordinator.sensors_payload

        rain = sensors.get("intenzita_srazek", {}).get("value")
        if rain is not None and rain > 0:
            return "pouring" if rain > 2 else "rainy"

        solar = sensors.get("slunecni_zareni", {}).get("value")
        if solar is not None:
            if solar > 300:
                return "sunny"
            if solar > 100:
                return "partlycloudy"

        uv = sensors.get("uv_index", {}).get("value")
        if uv is not None and uv > 5:
            return "sunny"

        wind = sensors.get("vitr_rychlost", {}).get("value")
        if wind is not None and wind > 10:
            return "windy"

        return "cloudy"

    @property
    def native_temperature(self) -> float | None:
        """Vrací venkovní teplotu (sjednoceno s názvy v const.py)."""
        sensor = self.coordinator.sensors_payload.get("teplota_vnejsi")
        return sensor.get("value") if sensor else None

    @property
    def native_pressure(self) -> float | None:
        """Vrací relativní tlak vzduchu."""
        sensor = self.coordinator.sensors_payload.get("tlak_relativni")
        return sensor.get("value") if sensor else None

    @property
    def humidity(self) -> float | None:
        """Vrací venkovní vlhkost (sjednoceno s názvy v const.py)."""
        sensor = self.coordinator.sensors_payload.get("vlhkost_vnejsi")
        return sensor.get("value") if sensor else None

    @property
    def native_wind_speed(self) -> float | None:
        """Vrací rychlost větru přepočtenou na km/h pro weather platformu HA."""
        sensor = self.coordinator.sensors_payload.get("vitr_rychlost")
        if sensor and sensor.get("value") is not None:
            return round(float(sensor["value"]) * 3.6, 1)
        return None

    @property
    def native_wind_gust(self) -> float | None:
        """Vrací nárazy větru přepočtené na km/h pro weather platformu HA."""
        sensor = self.coordinator.sensors_payload.get("vitr_narazy")
        if sensor and sensor.get("value") is not None:
            return round(float(sensor["value"]) * 3.6, 1)
        return None

    @property
    def wind_bearing(self) -> float | None:
        """Vrací směr větru ve stupních."""
        sensor = self.coordinator.sensors_payload.get("vitr_smer")
        return sensor.get("value") if sensor else None

    #
    # === STANDARD HA WEATHERENTITY – NATIVNÍ JEDNOTKY ===
    #

    @property
    def temperature_unit(self) -> str:
        return "°C"

    @property
    def pressure_unit(self) -> str:
        return "hPa"

    @property
    def wind_speed_unit(self) -> str:
        return "km/h"

    @property
    def visibility_unit(self) -> str:
        return "km"

    @property
    def precipitation_unit(self) -> str:
        return "mm"

    #
    # === EXTRA ATRIBUTY SCHVÁLENÉ PRO FRONTENDOVOU KARTU ===
    #

    @property
    def extra_state_attributes(self) -> dict:
        """Doplňkové atributy, které standardní weather neumí předat."""
        attrs: dict = {}

        if ATTR_STATION_LOCATION in self.coordinator.station_metadata:
            attrs[ATTR_STATION_LOCATION] = self.coordinator.station_metadata[ATTR_STATION_LOCATION]
        
        attrs[ATTR_API_TIMESTAMP] = datetime.now().isoformat()

        daily_rain = None
        if "srazky_den" in self.coordinator.station_metadata:
            daily_rain = self.coordinator.station_metadata["srazky_den"]
        else:
            raw = getattr(self.coordinator, "data", {})
            if isinstance(raw, dict):
                daily_rain = raw.get("SrazkyDen")

        attrs[ATTR_DAILY_RAIN] = daily_rain if daily_rain is not None else 0

        # Publikace intervalu pro statistiky (podle konfigurace integrace)
        attrs["statistics_interval"] = self._entry.options.get(CONF_STATISTICS_INTERVAL)

        # Ponecháme základní update_interval pro stabilitu (z options, ne natvrdo)
        attrs["update_interval"] = self._entry.options.get("update_interval", 5)

        station_prefix = self.entity_id.split(".")[1]
        sensors_meta: list[dict] = []
        
        for sid, meta in SENSOR_DEFINITIONS.items():
            opts = self._entry.options.get(CONF_SENSORS, {}).get(sid, {})
            
            if not opts.get("visible", meta.get("type") == "primary" or True):
                continue

            entity_id = f"sensor.{station_prefix}_{sid}"
            sensors_meta.append({
                "id": sid,
                "entity_id": entity_id,
                "type": meta.get("type", "secondary"),
                "order": opts.get("order", meta.get("order", 999)),
                "visible": True,
            })

        for sid, payload in self.coordinator.sensors_payload.items():
            if any(s["id"] == sid for s in sensors_meta):
                continue
                
            meta = payload.get("meta", {})
            if not meta.get("visible", True):
                continue

            entity_id = f"sensor.{station_prefix}_{sid}"
            sensors_meta.append({
                "id": sid,
                "entity_id": entity_id,
                "type": meta.get("type", "secondary"),
                "order": meta.get("order", 999),
                "visible": True,
            })

        sensors_meta.sort(key=lambda x: x["order"])

        attrs["sensors"] = sensors_meta

        attrs["history_queue_length"] = self.coordinator._diag_queue_length
        attrs["history_worker_running"] = self.coordinator._diag_worker_running
        attrs["history_missing_count"] = self.coordinator._diag_missing_count
        attrs["history_last_batch_size"] = self.coordinator._diag_last_batch_size
        attrs["history_last_write_ts"] = (
            self.coordinator._diag_last_write_ts.isoformat()
            if self.coordinator._diag_last_write_ts
            else None
        )
        
        return attrs
