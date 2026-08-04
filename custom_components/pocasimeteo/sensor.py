"""Weather platform for PočasíMeteo."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_STATION, ATTR_STATION_LOCATION, ATTR_API_TIMESTAMP, ATTR_DAILY_RAIN
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

        # ODCHYLKA od čistého weather standardu: Pokud v budoucnu propojíte options_flow 
        # s externí forecast entitou, deklarujeme, že tato entita podporuje asynchronní předpověď.
        self._attr_supported_features = WeatherEntityFeature.FORECAST_DAILY

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self._entry.data.get(CONF_STATION) or "PočasíMeteo",
            manufacturer="PočasíMeteo",
            model="Meteostanice",
        )

    #
    # === STANDARD HA WEATHERENTITY API ===
    # Tyto vlastnosti Home Assistant automaticky publikuje ve stavovém objektu na frontend.
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
        """Vrací venkovní teplotu (sjednoceno s const.py)."""
        sensor = self.coordinator.sensors_payload.get("teplota_vnejsi")
        return sensor.get("value") if sensor else None

    @property
    def native_pressure(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("tlak_relativni")
        return sensor.get("value") if sensor else None

    @property
    def humidity(self) -> float | None:
        """Vrací venkovní vlhkost (sjednoceno s const.py)."""
        sensor = self.coordinator.sensors_payload.get("vlhkost_vnejsi")
        return sensor.get("value") if sensor else None

    @property
    def native_wind_speed(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("vitr_rychlost")
        return sensor.get("value") if sensor else None

    @property
    def native_wind_gust(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("vitr_narazy")
        return sensor.get("value") if sensor else None

    @property
    def wind_bearing(self) -> float | None:
        sensor = self.coordinator.sensors_payload.get("vitr_smer")
        return sensor.get("value") if sensor else None

    #
    # === EXTRA ATRIBUTY SCHVÁLENÉ PRO FRONTENDOVOU KARTU ===
    #

    @property
    def extra_state_attributes(self) -> dict:
        """Doplňkové minimální atributy, které standardní weather neumí předat."""
        attrs: dict = {}

        # 1. Název lokality vrácený ze serveru a čas poslední aktualizace API
        if ATTR_STATION_LOCATION in self.coordinator.station_metadata:
            attrs[ATTR_STATION_LOCATION] = self.coordinator.station_metadata[ATTR_STATION_LOCATION]
        
        attrs[ATTR_API_TIMESTAMP] = datetime.now().isoformat()

        # 2. Celkové srážky za aktuální den ze syrových dat koordinátoru
        raw_data = self.coordinator.data
        if isinstance(raw_data, dict):
            attrs[ATTR_DAILY_RAIN] = raw_data.get("SrazkyDen", 0)

        # 3. Dynamický seznam senzorů s jejich reálnými entity_id v HA systému.
        # ARCHITEKTURA FRONTENDU: Karta prochází toto pole a okamžitě ví, ze kterých 
        # sensor entit má načítat historii pro vykreslení jednotlivých dlaždic grafů.
        station_name_slug = (self._entry.data.get(CONF_STATION) or "").lower().replace(" ", "_")
        sensors_meta: list[dict] = []
        
        for sid, payload in self.coordinator.sensors_payload.items():
            meta = payload.get("meta", {})
            
            # Sestavíme předpokládané entity_id generované platformou sensor
            entity_id = f"sensor.pocasimeteo_{sid}"
            
            # Ověříme, zda entita v HA opravdu existuje a má stav
            if self.hass.states.get(entity_id) is None:
                continue

            sensors_meta.append({
                "id": sid,
                "entity_id": entity_id,
                "type": meta.get("type", "secondary"),
                "order": meta.get("order", 999),
                "visible": meta.get("visible", True),
            })

        attrs["sensors"] = sensors_meta
        return attrs
