"""Sensor entities for PočasíMeteo integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, SENSOR_DEFINITIONS, get_dynamic_sensor_meta, CONF_STATION
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo sensor entities dynamically based on API data."""
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    # Pokud koordinátor nemá žádná data, nemůžeme nic vytvořit
    if not coordinator.data:
        _LOGGER.error("pocasimeteo.sensor: No data available in coordinator to create entities")
        return

    station_name = entry.data.get(CONF_STATION, "Meteostanice")
    entities: list[PocasimeteoSensor] = []

    _LOGGER.debug(
        "pocasimeteo.sensor: Starting dynamic entity creation for entry_id=%s",
        entry.entry_id,
    )

    # Procházíme klíče, které reálně vrátil koordinátor z API
    for sid in coordinator.data.keys():
        meta = SENSOR_DEFINITIONS.get(sid)

        if meta is None:
            # Dynamický senzor (např. Te1-Te5, Co2, Pm1 atd. – získají se z helperu)
            meta = get_dynamic_sensor_meta(sid)
            _LOGGER.debug("pocasimeteo.sensor: Creating dynamic sensor id=%s", sid)
        else:
            _LOGGER.debug("pocasimeteo.sensor: Creating standard sensor id=%s", sid)

        entities.append(
            PocasimeteoSensor(
                coordinator=coordinator,
                sensor_id=sid,
                meta=meta,
                station_name=station_name,
            )
        )

    _LOGGER.info("pocasimeteo.sensor: Registering %d sensors to Home Assistant", len(entities))
    async_add_entities(entities)


class PocasimeteoSensor(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], SensorEntity):
    """Representation of a PočasíMeteo sensor integrated via CoordinatorEntity."""

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        sensor_id: str,
        meta: dict[str, Any],
        station_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        
        # Nastavení základních atributů entity
        self._attr_name = f"{station_name} {meta['name']}"
        self._attr_icon = meta["icon"]
        self._attr_native_unit_of_measurement = meta["unit"] if meta["unit"] else None
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{sensor_id}"
        
        # Povolíme dlouhodobé statistiky (LTS), pokud je hodnota číselná
        self._attr_state_class = SensorStateClass.MEASUREMENT

        # Seskupení všech senzorů pod jedno fyzické zařízení v HA rozhraní
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=station_name,
            manufacturer="PočasíMeteo.cz",
            model="Meteostanice",
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor's current value directly from coordinator data."""
        if not self.coordinator.data or self._sensor_id not in self.coordinator.data:
            return None
        return self.coordinator.data[self._sensor_id].get("value")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return min/max statistics and timestamp from coordinator data."""
        if not self.coordinator.data or self._sensor_id not in self.coordinator.data:
            return {}
        return self.coordinator.data[self._sensor_id].get("attributes", {})
