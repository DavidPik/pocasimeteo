"""Weather platform for PočasíMeteo."""

from __future__ import annotations

import logging
from datetime import datetime

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

    async_add_entities([PocasimeteoWeather(coordinator, entry)])


class PocasimeteoWeather(WeatherEntity):
    """Hlavní weather entita pro PočasíMeteo."""

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
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

    #
    # === STANDARD HA WEATHERENTITY API ===
    #

    @property
    def condition(self) -> str | None:
        """Odvozený stav počasí podle senzorů."""
        sensors = self._coordinator.sensors_payload

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
    def temperature(self) -> float | None:
        sensor = self._coordinator.sensors_payload.get("teplota_venkovni")
        return sensor.get("value") if sensor else None

    @property
    def pressure(self) -> float | None:
        sensor = self._coordinator.sensors_payload.get("tlak_relativni")
        return sensor.get("value") if sensor else None

    @property
    def humidity(self) -> float | None:
        sensor = self._coordinator.sensors_payload.get("vlhkost_venkovni")
        return sensor.get("value") if sensor else None

    @property
    def wind_speed(self) -> float | None:
        sensor = self._coordinator.sensors_payload.get("vitr_rychlost")
        return sensor.get("value") if sensor else None

    @property
    def wind_gust(self) -> float | None:
        sensor = self._coordinator.sensors_payload.get("vitr_narazy")
        return sensor.get("value") if sensor else None

    @property
    def wind_bearing(self) -> float | None:
        sensor = self._coordinator.sensors_payload.get("vitr_smer")
        return sensor.get("value") if sensor else None

    #
    # === STANDARD HA WEATHERENTITY – JEDNOTKY ===
    #

    @property
    def temperature_unit(self) -> str:
        return "°C"

    @property
    def pressure_unit(self) -> str:
        return "hPa"

    @property
    def wind_speed_unit(self) -> str:
        return "m/s"

    @property
    def visibility_unit(self) -> str:
        return "km"

    @property
    def precipitation_unit(self) -> str:
        return "mm"

    #
    # === KARTA: extra_state_attributes – vše níže je přidáno kvůli frontendové kartě ===
    #

    @property
    def extra_state_attributes(self) -> dict:
        """Doplňkové atributy pro frontendovou kartu PočasíMeteo."""

        attrs: dict = dict(self._coordinator.station_metadata)

        # === KARTA: karta potřebuje timestamp ===
        attrs["timestamp"] = datetime.now().isoformat()

        # === KARTA: karta potřebuje srážky za den ===
        raw = self._coordinator.data
        if isinstance(raw, dict):
            attrs["srazky_den"] = raw.get("SrazkyDen", 0)

        # === KARTA: karta potřebuje hodnoty přímo v atributech weather entity ===
        attrs["temperature"] = self.temperature
        attrs["pressure"] = self.pressure
        attrs["humidity"] = self.humidity
        attrs["wind_speed"] = self.wind_speed
        attrs["wind_gust"] = self.wind_gust
        attrs["wind_bearing"] = self.wind_bearing

        # === KARTA: karta potřebuje doplňkové hodnoty ze senzorů ===
        sensors = self._coordinator.sensors_payload
        attrs["precipitation"] = sensors.get("intenzita_srazek", {}).get("value")
        attrs["solar_radiation"] = sensors.get("slunecni_zareni", {}).get("value")
        attrs["uv_index"] = sensors.get("uv_index", {}).get("value")

        # === KARTA: karta používá update_interval ===
        attrs["update_interval"] = 5

        # === KARTA: karta potřebuje seznam senzorů s entity_id ===
        station = self._entry.data.get("station_name") or ""
        station_id = station.lower()

        sensors_meta: list[dict] = []
        for sid, payload in sensors.items():
            meta = payload.get("meta", {})

            entity_id = f"sensor.{station_id}_{sid}"
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
