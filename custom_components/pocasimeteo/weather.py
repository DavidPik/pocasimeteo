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
        self._attr_name = coordinator.station_metadata.get("station_name") or "PočasíMeteo"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._coordinator.station_metadata.get("station_name") or "PočasíMeteo",
            manufacturer="PočasíMeteo",
            model="Meteostanice",
        )

    @property
    def temperature(self):
        sensor = self._coordinator.sensors_payload.get("teplota_vnejsi")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def pressure(self):
        sensor = self._coordinator.sensors_payload.get("tlak")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def humidity(self):
        sensor = self._coordinator.sensors_payload.get("vlhkost")
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

        # připravíme metadata pro kartu (sensors pole)
        sensors_meta = []
        for sid, payload in self._coordinator.sensors_payload.items():
            meta = payload["meta"]
            sensors_meta.append(
                {
                    "id": sid,
                    "entity_id": f"sensor.{self._entry.entry_id}_{sid}",
                    "type": meta.get("type", "secondary"),
                    "order": meta.get("order", 999),
                    "visible": True,
                }
            )

        attrs["sensors"] = sensors_meta
        return attrs
