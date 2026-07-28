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

from .const import DOMAIN, CONF_STATION, CONF_UPDATE_INTERVAL
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
    """Representation of PočasíMeteo weather summary."""

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
            model="Meteion-Station",
        )

    @property
    def condition(self) -> str | None:
        return self._get_value("intenzita_srazek")

    @property
    def native_temperature(self) -> float | None:
        return self._get_value("teplota_vnejsi")

    @property
    def native_pressure(self) -> float | None:
        return self._get_value("tlak_relativni")

    @property
    def humidity(self) -> float | None:
        return self._get_value("vlhkost_vnejsi")

    @property
    def native_wind_speed(self) -> float | None:
        return self._get_value("vitr_rychlost")

    @property
    def wind_bearing(self) -> float | str | None:
        return self._get_value("vitr_smer")

    @property
    def native_precipitation(self) -> float | None:
        return self._get_value("intenzita_srazek")

    def _get_value(self, sid: str) -> Any:
        if not self.coordinator.data or sid not in self.coordinator.data:
            return None
        return self.coordinator.data[sid].get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose combined attributes and structural metadata formatted precisely for the custom card."""
        attrs: dict[str, Any] = {}
        
        # DEFINICE PROMĚNNÝCH NA ZAČÁTKU METODY:
        primary_entities: list[str] = []
        secondary_entities: list[str] = []
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

        from homeassistant.helpers import entity_registry as er, device_registry as dr
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        device = dev_reg.async_get_device(identifiers={(DOMAIN, self.coordinator.entry.entry_id)})
        device_id = device.id if device else None
        device_entities = er.async_entries_for_device(ent_reg, device_id) if device_id else []

        sorted_sensors = sorted(
            self.coordinator.data.items(),
            key=lambda x: x[1].get("order", 200)
        )

        for sid, payload in sorted_sensors:
            val = payload.get("value")
            
            attrs[f"{sid}_value"] = val
            for key, val_attr in payload.get("attributes", {}).items():
                attrs[f"{sid}_{key.lower()}"] = val_attr

            if sid == "vitr_smer":
                attrs["vitr_smer_avg"] = val
                attrs["vitr_smer_mode"] = val
                attrs["vitr_smer_var"] = 0.0

            real_entity_id = f"sensor.{DOMAIN}_{sid}"
            for entry in device_entities:
                if entry.domain == "sensor" and entry.unique_id.endswith(f"_{sid}"):
                    real_entity_id = entry.entity_id
                    break

            s_type = payload.get("type", "secondary")
            if s_type == "primary":
                primary_entities.append(real_entity_id)
            else:
                secondary_entities.append(real_entity_id)

            sensors_metadata.append({
                "id": sid,
                "entity_id": real_entity_id,
                "type": s_type,
                "order": payload.get("order", 200)
            })

        sensors_metadata.sort(key=lambda x: x["order"])
        
        attrs["primary_sensors"] = primary_entities
        attrs["secondary_sensors"] = secondary_entities
        attrs["sensors"] = sensors_metadata

        return attrs
