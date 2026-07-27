"""Weather entity for PočasíMeteo integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.weather import (
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfPrecipitationDepth,
)

from .const import DOMAIN, CONF_STATION
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

    station_name = entry.data.get(CONF_STATION, "Meteostanice")

    async_add_entities([PocasimeteoWeather(coordinator, station_name)])


class PocasimeteoWeather(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], WeatherEntity):
    """Representation of PočasíMeteo weather summary using CoordinatorEntity."""

    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature(0)

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator, station_name: str) -> None:
        """Initialize the weather entity."""
        super().__init__(coordinator)
        self._station_name = station_name

        self._attr_name = station_name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_weather"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=station_name,
            manufacturer="PočasíMeteo.cz",
            model="Meteostanice",
        )

    @property
    def condition(self) -> str | None:
        """Return the current condition based on rain."""
        srazky = self._get_value("rainintensity")
        if srazky is not None and srazky > 0:
            return "rainy"
        return "sunny"

    @property
    def native_temperature(self) -> float | None:
        return self._get_value("teplotavnejsi")

    @property
    def native_pressure(self) -> float | None:
        return self._get_value("tlakrel")

    @property
    def humidity(self) -> float | None:
        return self._get_value("vlhkostvnejsi")

    @property
    def native_wind_speed(self) -> float | None:
        return self._get_value("vitr")

    @property
    def wind_bearing(self) -> float | str | None:
        return self._get_value("vitrsmer")

    @property
    def native_precipitation(self) -> float | None:
        return self._get_value("rainintensity")

    def _get_value(self, sid: str) -> Any:
        """Helper to get a value from coordinator data safely."""
        if not self.coordinator.data or sid not in self.coordinator.data:
            return None
        return self.coordinator.data[sid].get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose combined attributes and structural metadata with real entity IDs."""
        attrs: dict[str, Any] = {}
        sensors_metadata: list[dict[str, Any]] = []

        metadata = getattr(self.coordinator, "station_metadata", {})
        attrs["lokalita_stanice"] = metadata.get("lokalita") or self._station_name
        attrs["url_webkamera"] = metadata.get("webcamera_url") or ""
        attrs["station_name"] = self._station_name
        attrs["config_entry_id"] = self.coordinator.entry.entry_id
        attrs["update_interval"] = self.coordinator.entry.options.get(CONF_UPDATE_INTERVAL, 5)
        
        import datetime
        attrs["timestamp"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        attrs["srazky_den"] = metadata.get("srazky_den", 0.0)

        if not self.coordinator.data:
            return attrs

        # Načteme si systémový registr entit Home Assistantu
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(self.hass)

        for sid, payload in self.coordinator.data.items():
            val = payload.get("value")
            
            # Hodnoty v surové podobě pro přímé čtení v kartě (např. teplota_venkovni_value)
            attrs[f"{sid}_value"] = val

            # Vyhledáme reálné entity_id v systému podle unique_id integrace
            # Tímto krokem bezpečně zachytíme prefixy jako sensor.venku_gar632_...
            unique_id = f"{self.coordinator.entry.entry_id}_{sid}"
            entity_entry = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
            
            # Pokud registr entitu zatím nezná, poskládáme bezpečný fallback
            real_entity_id = entity_entry or f"sensor.{DOMAIN}_{sid}"

            sensors_metadata.append({
                "id": sid,                         # Čisté ID (např. teplota_venkovni)
                "entity_id": real_entity_id,       # Skutečné ID v systému (např. sensor.venku_gar632_teplota_venkovni)
                "type": payload.get("type", "secondary"),
                "order": payload.get("order", 200)
            })

        sensors_metadata.sort(key=lambda x: x["order"])
        attrs["sensors"] = sensors_metadata

        return attrs
