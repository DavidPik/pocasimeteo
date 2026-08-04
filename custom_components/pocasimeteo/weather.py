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
    # Stav počasí (condition)
    #
    @property
    def condition(self) -> str | None:
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

    #
    # Hlavní hodnoty – nové WeatherEntity API (native_* + *_unit)
    #
    @property
    def native_temperature(self) -> float | None:
        """Aktuální venkovní teplota."""
        sensor = self._coordinator.sensors_payload.get("teplota_venkovni")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def temperature_unit(self) -> str:
        """Jednotka teploty."""
        # uložená v options/config flow
        return self._entry.options.get("temperature_unit", "°C")

    @property
    def native_pressure(self) -> float | None:
        """Relativní tlak."""
        sensor = self._coordinator.sensors_payload.get("tlak_relativni")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def pressure_unit(self) -> str:
        """Jednotka tlaku."""
        return self._entry.options.get("barometric_pressure_unit", "hPa")

    @property
    def humidity(self) -> float | None:
        """Relativní vlhkost venkovní."""
        sensor = self._coordinator.sensors_payload.get("vlhkost_venkovni")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def native_wind_speed(self) -> float | None:
        """Rychlost větru."""
        sensor = self._coordinator.sensors_payload.get("vitr_rychlost")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def native_wind_gust(self) -> float | None:
        """Nárazy větru."""
        sensor = self._coordinator.sensors_payload.get("vitr_narazy")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def native_wind_bearing(self) -> float | None:
        """Směr větru (°)."""
        sensor = self._coordinator.sensors_payload.get("vitr_smer")
        if not sensor:
            return None
        return sensor.get("value")

    @property
    def wind_speed_unit(self) -> str:
        """Jednotka rychlosti větru."""
        return self._entry.options.get("wind_speed_unit", "km/h")

    @property
    def visibility_unit(self) -> str:
        """Jednotka viditelnosti."""
        return self._entry.options.get("visibility_unit", "km")

    @property
    def precipitation_unit(self) -> str:
        """Jednotka srážek."""
        return self._entry.options.get("precipitation_unit", "mm")

    #
    # Extra atributy pro kartu a metadata stanice
    #
    @property
    def extra_state_attributes(self) -> dict:
        """Doplňkové atributy pro frontendovou kartu PočasíMeteo."""
        attrs: dict = dict(self._coordinator.station_metadata or {})

        # timestamp měření
        attrs["timestamp"] = datetime.now().isoformat()

        # srážky za den (API klíč SrazkyDen)
        raw = self._coordinator.data
        if isinstance(raw, dict):
            attrs["srazky_den"] = raw.get("SrazkyDen", 0)

        # název stanice (pro jistotu i zde)
        station = self._entry.data.get("station_name") or attrs.get("station_name")
        if station:
            attrs["station_name"] = station

        # připravíme metadata pro kartu (sensors pole)
        sensors_meta: list[dict] = []
        for sid, payload in self._coordinator.sensors_payload.items():
            meta = payload.get("meta", {})

            # pokud entita neexistuje v hass.states, přeskočíme ji
            # entity_id v HA je ve tvaru sensor.gar632_teplota_venkovni atd.
            entity_id = f"sensor.{station}_{sid}" if station else None
            if not entity_id or self.hass.states.get(entity_id) is None:
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
