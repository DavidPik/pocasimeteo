"""Weather platform for PočasíMeteo."""

from __future__ import annotations

import logging

from homeassistant.components.weather import WeatherEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import PocasimeteoDataUpdateCoordinator

from datetime import datetime

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo weather entity."""
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    async_add_entities([PočasíMeteoWeather(coordinator, entry)])


class PočasíMeteoWeather(WeatherEntity):
    """Hlavní weather entita pro PočasíMeteo."""

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry

        self._attr_unique_id = entry.entry_id
        self._attr_name = self._entry.data.get("station_name") or "PočasíMeteo"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._entry.data.get("station_name") or "PočasíMeteo",
            manufacturer="PočasíMeteo",
            model="Meteostanice",
        )

    @property
    def condition(self):
        """Odvozený stav počasí podle senzorů."""
        sensors = self._coordinator.sensors_payload

        # 1) Déšť
        rain = sensors.get("intenzita_srazek", {}).get("value")
        if rain is not None and rain > 0:
            if rain > 2:
                return "pouring"
            return "rainy"

        # 2) Sluneční záření
        solar = sensors.get("slunecni_zareni", {}).get("value")
        if solar is not None:
            if solar > 300:
                return "sunny"
            if solar > 100:
                return "partlycloudy"

        # 3) UV index
        uv = sensors.get("uv_index", {}).get("value")
        if uv is not None and uv > 5:
            return "sunny"

        # 4) Vítr
        wind = sensors.get("vitr_rychlost", {}).get("value")
        if wind is not None and wind > 10:
            return "windy"

        # 5) Default
        return "cloudy"

    @property
    def temperature(self):
        sensor = self._coordinator.sensors_payload.get("teplota_vnejsi")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def pressure(self):
        sensor = self._coordinator.sensors_payload.get("tlak_relativni")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def humidity(self):
        sensor = self._coordinator.sensors_payload.get("vlhkost_vnejsi")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def wind_speed(self):
        sensor = self._coordinator.sensors_payload.get("vitr_rychlost")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def wind_gust(self):
        sensor = self._coordinator.sensors_payload.get("vitr_narazy")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def wind_bearing(self):
        sensor = self._coordinator.sensors_payload.get("vitr_smer")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def extra_state_attributes(self):
        attrs = dict(self._coordinator.station_metadata)

        # timestamp měření
        attrs["timestamp"] = datetime.now().isoformat()

        # srážky za den (API klíč SrazkyDen)
        raw = self._coordinator.data
        if isinstance(raw, dict):
            attrs["srazky_den"] = raw.get("SrazkyDen", 0)

        # připravíme metadata pro kartu (sensors pole)
        station = self._entry.data.get("station_name")
        sensors_meta = []
        for sid, payload in self._coordinator.sensors_payload.items():
            meta = payload["meta"]

            # pokud entita neexistuje v hass.states, přeskočíme ji
            entity_id = f"sensor.{station}_{sid}"
            if self.hass.states.get(entity_id) is None:
                continue
            
            sensors_meta.append(
                {
                    "id": sid,
                    "entity_id": entity_id,
                    "type": meta.get("type", "secondary"),
                    "order": meta.get("order", 999),
                    "visible": meta.get("visible", True),
                }
            )

        attrs["sensors"] = sensors_meta

        return attrs
