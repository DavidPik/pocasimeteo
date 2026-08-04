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

        # Deklarujeme podporu pro asynchronní předpověď (forecast), pokud se v budoucnu napojí
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
        """Vrací rychlost větru."""
        sensor = self.coordinator.sensors_payload.get("vitr_rychlost")
        return sensor.get("value") if sensor else None

    @property
    def native_wind_gust(self) -> float | None:
        """Vrací nárazy větru."""
        sensor = self.coordinator.sensors_payload.get("vitr_narazy")
        return sensor.get("value") if sensor else None

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
        return "m/s"

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
        """Doplňkové minimální atributy, které standardní weather neumí předat."""
        attrs: dict = {}

        # 1. Název lokality vrácený ze serveru a čas poslední aktualizace API
        if ATTR_STATION_LOCATION in self.coordinator.station_metadata:
            attrs[ATTR_STATION_LOCATION] = self.coordinator.station_metadata[ATTR_STATION_LOCATION]
        
        attrs[ATTR_API_TIMESTAMP] = datetime.now().isoformat()

        # 2. Celkové srážky za aktuální den získané bezpečně z koordinátoru
        if hasattr(self.coordinator, "data") and isinstance(self.coordinator.data, dict):
            attrs[ATTR_DAILY_RAIN] = self.coordinator.data.get("SrazkyDen", 0)
        else:
            attrs[ATTR_DAILY_RAIN] = 0

        # Ponecháme základní update_interval pro stabilitu
        attrs["update_interval"] = 5

        # 3. Dynamický seznam senzorů s jejich reálnými sjednocenými entity_id v HA systému.
        # ARCHITEKTURA FRONTENDU: Karta prochází toto pole a okamžitě ví, ze kterých 
        # sensor entit má načítat historii pro vykreslení jednotlivých dlaždic grafů.
        sensors_meta: list[dict] = []
        
        # ARCHITEKTURA FRONTENDU: Získáme přesný prefix stanice (např. "gar632") 
        # přímo z vlastního názvu této weather entity (weather.gar632 -> gar632)
        station_prefix = self.entity_id.split(".")[1]
        sensors_meta: list[dict] = []
        
        for sid, payload in self.coordinator.sensors_payload.items():
            meta = payload.get("meta", {})
            
            # Oprava provázání: Sestavíme reálné ID, pod kterým senzory v HA aktuálně žijí
            entity_id = f"sensor.{station_slug}_{sid}"
            
            # Ověříme, zda entita v HA opravdu existuje a má platný stav
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
