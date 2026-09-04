"""Platforma pro hlavní weather entitu integrace PočasíMeteo."""
from __future__ import annotations

import logging
from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SENSOR_DEFINITIONS, API_TO_INTERNAL_MAPPING, CONF_SENSORS
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    """Nastavení weather platformy na základě konfigurace."""
    # Bezpečně vytáhneme koordinátor ze slovníku nebo přímo z hass.data
    data_source = hass.data[DOMAIN][entry.entry_id]
    coordinator = data_source if not isinstance(data_source, dict) else data_source.get("coordinator")
    
    if coordinator is None:
        _LOGGER.error("Koordinátor nebyl v hass.data nalezen při zavádění weather entity")
        return

    async_add_entities([PocasimeteoWeather(coordinator, entry)])


class PocasimeteoWeather(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], WeatherEntity):
    """Hlavní stavová entita počasí meteostanice PočasíMeteo."""

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator, entry):
        """Inicializace entity s přímým předáním config entry."""
        super().__init__(coordinator)
        self._entry = entry
        
        # OSTRÁ OPRAVA: Kód stanice odvodíme přímo z konfiguračního objektu entry, nikoliv z dict koordinátoru
        station_prefix = entry.title.lower().strip().replace(" ", "_")

        self._attr_unique_id = f"{entry.entry_id}_weather"
        self.entity_id = f"weather.{station_prefix}"
        self._attr_name = entry.title

        # ARCHITEKTURA CORE HA: Tato entita poskytuje pouze lokální živá data.
        self._attr_supported_features = 0
        self._attr_device_info = coordinator.station_metadata.get("device_info")

        # ARCHITEKTURA CORE HA: Tato entita poskytuje pouze lokální živá data.
        # Předpověď počasí je deaktivována (0) pro zamezení chyb NotImplementedError ve WS.
        self._attr_supported_features = 0
        self._attr_device_info = coordinator.station_metadata.get("device_info")

    @property
    def state(self) -> str | None:
        """Vrací aktuální stav počasí převedený z weather entity."""
        return self.coordinator.sensors_payload.get("weather", {}).get("value", "cloudy")

    @property
    def native_temperature(self) -> float | None:
        """Vrací aktuální vnější teplotu."""
        sensor = self.coordinator.sensors_payload.get("teplota_vnejsi")
        return float(sensor["value"]) if sensor and sensor.get("value") is not None else None

    @property
    def native_temperature_unit(self) -> str:
        """Vrací jednotku teploty."""
        return "°C"

    @property
    def humidity(self) -> float | None:
        """Vrací aktuální vnější vlhkost."""
        sensor = self.coordinator.sensors_payload.get("vlhkost_vnejsi")
        return float(sensor["value"]) if sensor and sensor.get("value") is not None else None

    @property
    def native_pressure(self) -> float | None:
        """Vrací aktuální relativní tlak vzduchu."""
        sensor = self.coordinator.sensors_payload.get("tlak_relativni")
        return float(sensor["value"]) if sensor and sensor.get("value") is not None else None

    @property
    def native_pressure_unit(self) -> str:
        """Vrací jednotku tlaku."""
        return "hPa"

    @property
    def native_wind_speed(self) -> float | None:
        """Vrací průměrnou rychlost větru přepočtenou na km/h dle standardu HA weather entit."""
        sensor = self.coordinator.sensors_payload.get("vitr_rychlost")
        if sensor and sensor.get("value") is not None:
            return round(float(sensor["value"]) * 3.6, 1)
        return None

    @property
    def native_wind_gust(self) -> float | None:
        """Vrací nárazy větru přepočtené na km/h dle standardu HA weather entit."""
        sensor = self.coordinator.sensors_payload.get("vitr_narazy")
        if sensor and sensor.get("value") is not None:
            return round(float(sensor["value"]) * 3.6, 1)
        return None

    @property
    def wind_speed_unit(self) -> str:
        """Vrací jednotku rychlosti větru vyžadovanou HA."""
        return "km/h"

    @property
    def wind_bearing(self) -> int | None:
        """Vrací aktuální azimut směru větru ve stupních (0-360)."""
        sensor = self.coordinator.sensors_payload.get("vitr_smer")
        return int(sensor["value"]) if sensor and sensor.get("value") is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """
        ARCHITEKTURA FRONTENDU: Centralizovaná distribuce metadat pro Lovelace kartu.
        Generuje dvě samostatné struktury: 'sensors' (konfigurace a barvy dlaždic) 
        a 'sensor_stats' (čistá Key-Value mapa analytických výsledků z DB).
        """
        station_prefix = self.coordinator.entry.title.lower().strip().replace(" ", "_")
        stats_dict = self.coordinator.station_metadata.get("sensor_stats", {})

        # 1. STRUKTURA: Statická konfigurace vzhledu, barev a řazení grafů
        sensors_meta = []
        for sid, meta in SENSOR_DEFINITIONS.items():
            opts = self._entry.options.get(CONF_SENSORS, {}).get(sid, {})
            if not opts.get("visible", meta.get("type") == "primary" or True):
                continue

            internal_sid = API_TO_INTERNAL_MAPPING.get(meta["api_key"].lower(), meta["api_key"].lower())
            sensors_meta.append({
                "id": sid,
                "entity_id": f"sensor.{station_prefix}_{internal_sid}",
                "type": meta.get("type", "secondary"),
                "order": opts.get("order", meta.get("order", 999)),
                "visible": True,
                "graph_color": opts.get("color", meta.get("color", "#3b82f6")),
                "graph_style": opts.get("style", "smooth")
            })

        # Přidáme do seznamu konfigurací i případná dynamická (objevená) čidla
        for sid, payload in self.coordinator.sensors_payload.items():
            if sid not in SENSOR_DEFINITIONS and sid != "weather":
                sensors_meta.append({
                    "id": sid,
                    "entity_id": f"sensor.{station_prefix}_{sid}",
                    "type": "secondary",
                    "order": 99,
                    "visible": True,
                    "graph_color": "#3b82f6",
                    "graph_style": "smooth"
                })

        # Seřadíme pole konfigurací dlaždic podle přání uživatele z Options Flow
        sensors_meta.sort(key=lambda x: x["order"])

        # Sestavení výsledného slovníku kořenových atributů weather entity
        attrs = {
            "lokalita_stanice": self.coordinator.station_metadata.get("lokalita_stanice"),
            "webcamera_url": self.coordinator.station_metadata.get("webcamera_url"),
            "srazky_den": self.coordinator.station_metadata.get("srazky_den", 0),
            "timestamp": self.coordinator.station_metadata.get("history_last_write_ts"),
            "statistics_interval": self.coordinator._statistics_interval,
            "update_interval": self.coordinator.update_interval.total_seconds() // 60 if self.coordinator.update_interval else 5,
            
            # DVĚ INTEGRÁLNÍ STRUKTURY PRO ULTRA RYCHLÉ VYKRESLENÍ KARTY LOVELACE
            "sensors": sensors_meta,
            "sensor_stats": stats_dict,
            
            # Podpora pro interní diagnostiku chodu background workeru
            "history_queue_length": self.coordinator._diag_queue_length,
            "history_worker_running": self.coordinator._diag_worker_running,
            "history_missing_count": self.coordinator._diag_missing_count,
            "history_last_batch_size": self.coordinator._diag_last_batch_size
        }
        return attrs
