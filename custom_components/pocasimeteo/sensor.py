"""Platforma pro samostatné senzory integrace PočasíMeteo."""
from __future__ import annotations

import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_DEFINITIONS,
    API_TO_INTERNAL_MAPPING,
    CONF_STATION,
    get_dynamic_sensor_meta,
)
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Nastavení senzorů na základě konfigurace integrace."""
    data_source = hass.data[DOMAIN][entry.entry_id]
    coordinator = (
        data_source if not isinstance(data_source, dict) else data_source.get("coordinator")
    )

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
        super().__init__(coordinator)
        self._sensor_id = sensor_id

        station_prefix = entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")

        internal_sid = API_TO_INTERNAL_MAPPING.get(sensor_id.lower(), sensor_id.lower())
        self._internal_sid = internal_sid

        self._attr_unique_id = f"{entry.entry_id}_{internal_sid}"
        self.entity_id = f"sensor.{station_prefix}_{internal_sid}"

        # Metadata senzoru
        if sensor_id in SENSOR_DEFINITIONS:
            meta = SENSOR_DEFINITIONS[sensor_id]
        else:
            meta = get_dynamic_sensor_meta(sensor_id)

        self._attr_name = meta.get("name", sensor_id)

        # --- OPRAVA JEDNOTEK ---
        unit = meta.get("unit")

        # Směr větru musí mít jednotku °
        if internal_sid == "vitr_smer":
            unit = "°"
            self._attr_device_class = None
            self._attr_state_class = None

        # Sluneční záření musí mít jednotku W/m²
        elif internal_sid == "slunecni_zareni":
            unit = "W/m²"
            self._attr_device_class = SensorDeviceClass.IRRADIANCE
            self._attr_state_class = SensorStateClass.MEASUREMENT

        # UV index má jednotku None správně
        elif internal_sid == "uv_index":
            unit = None
            self._attr_device_class = None
            self._attr_state_class = SensorStateClass.MEASUREMENT

        else:
            self._attr_device_class = meta.get("device_class")
            self._attr_state_class = meta.get("state_class")

        self._attr_native_unit_of_measurement = unit
        self._attr_icon = meta.get("icon")

        # Propojení s hlavním zařízením meteostanice v HA Jádru
        self._attr_device_info = coordinator.station_metadata.get("device_info")

    @property
    def native_value(self) -> float | str | None:
        return self.coordinator.sensors_payload.get(self._internal_sid, {}).get("value")

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        payload = self.coordinator.sensors_payload.get(self._internal_sid, {})
        attributes = payload.get("attributes", {})

        attrs = {
            "timestamp": attributes.get("timestamp")
        }

        if self._internal_sid == "vitr_smer":
            if "vitr_smer_avg" in attributes:
                attrs["vitr_smer_avg"] = attributes["vitr_smer_avg"]
            if "vitr_smer_mode" in attributes:
                attrs["vitr_smer_mode"] = attributes["vitr_smer_mode"]
            if "vitr_smer_var" in attributes:
                attrs["vitr_smer_var"] = attributes["vitr_smer_var"]
        else:
            if "min" in attributes:
                attrs["min"] = attributes["min"]
            if "max" in attributes:
                attrs["max"] = attributes["max"]

        return attrs
