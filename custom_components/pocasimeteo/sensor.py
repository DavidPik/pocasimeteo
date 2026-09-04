"""Platforma pro samostatné senzory integrace PočasíMeteo."""
from __future__ import annotations

import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SENSOR_DEFINITIONS, API_TO_INTERNAL_MAPPING
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Nastavení senzorů na základě konfigurace integrace."""
    data_source = hass.data[DOMAIN][entry.entry_id]
    coordinator = data_source if not isinstance(data_source, dict) else data_source.get("coordinator")
    
    if coordinator is None:
        _LOGGER.error("Koordinátor nebyl v hass.data nalezen při zavádění senzorů")
        return

    entities = []

    # 1. KROK: Vytvoření pevných (statických) senzorů z const.py
    for sid in SENSOR_DEFINITIONS:
        entities.append(PocasimeteoSensor(coordinator, entry, sid))

    # 2. KROK: Vytvoření dynamicky objevených senzorů z API payloadu
    for sid in coordinator.sensors_payload:
        if sid not in SENSOR_DEFINITIONS and sid != "weather":
            entities.append(PocasimeteoSensor(coordinator, entry, sid))

    async_add_entities(entities)


class PocasimeteoSensor(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], SensorEntity):
    """Reprezentace pasivního senzoru meteostanice PočasíMeteo."""

    def __init__(self, coordinator: PocasimeteoDataUpdateCoordinator, entry, sensor_id: str):
        """Inicializace senzoru s přímým předáním config entry."""
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        station_prefix = entry.title.lower().strip().replace(" ", "_")

        # Odvození interního ID entity (snake_case) přímo z entry
        internal_sid = API_TO_INTERNAL_MAPPING.get(sensor_id.lower(), sensor_id.lower())
        self._attr_unique_id = f"{entry.entry_id}_{internal_sid}"
        self.entity_id = f"sensor.{station_prefix}_{internal_sid}"

       # OSTRÁ OPRAVA: Metadata o jednotkách musíme číst ze statického const.py, 
        # protože při startu HA je sensors_payload ještě prázdný, což způsobovalo ztrátu jednotek (None)
        if sensor_id in SENSOR_DEFINITIONS:
            meta = SENSOR_DEFINITIONS[sensor_id]
        else:
            meta = get_dynamic_sensor_meta(sensor_id)

        self._attr_name = meta.get("name", sensor_id)
        self._attr_native_unit_of_measurement = meta.get("unit")
        self._attr_icon = meta.get("icon")
        self._attr_device_class = meta.get("device_class")
        self._attr_state_class = meta.get("state_class")
        
        # Propojení s hlavním zařízením meteostanice v HA Jádru
        self._attr_device_info = coordinator.station_metadata.get("device_info")

    @property
    def native_value(self) -> float | str | None:
        """Vrací aktuální syrovou hodnotu měření z paměti koordinátoru."""
        return self.coordinator.sensors_payload.get(self._sensor_id, {}).get("value")

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """
        ARCHITEKTURA STANDARDU HA: Senzor vrací pouze čistou časovou značku.
        Barvy grafů, styly čar a dlouhodobé statistiky byly kompletně 
        odsunuty do weather entity, aby se předešlo duplicitním zápisům změn.
        """
        payload = self.coordinator.sensors_payload.get(self._sensor_id, {})
        return {
            "timestamp": payload.get("attributes", {}).get("timestamp")
        }
