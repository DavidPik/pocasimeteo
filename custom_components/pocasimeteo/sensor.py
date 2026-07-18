"""Dynamic sensor platform for PočasíMeteo."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfTemperature,
    PERCENTAGE,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfIrradiance,
    CONCENTRATION_PARTS_PER_MILLION,
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# Mapování klíčů API → jednotky + device_class
SENSOR_DEFINITIONS = {
    "TeplotaVnejsi": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "VlhkostVnejsi": (PERCENTAGE, SensorDeviceClass.HUMIDITY),
    "TlakRel": (UnitOfPressure.HPA, SensorDeviceClass.PRESSURE),
    "Vitr": (UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
    "VitrNarazy": (UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.WIND_SPEED),
    "VitrSmer": ("°", None),
    "SrazkyIntenzita": ("mm/5min", None),  # opravený název senzoru
    "SlunZareni": (UnitOfIrradiance.WATTS_PER_SQUARE_METER, None),
    "UVindex": (None, None),
    "TeplotaVnitrni": (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "VlhkostVnitrni": (PERCENTAGE, SensorDeviceClass.HUMIDITY),
    "Co2": (CONCENTRATION_PARTS_PER_MILLION, SensorDeviceClass.CO2),
    "Pm1": (CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, SensorDeviceClass.PM1),
    "Pm2": (CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, SensorDeviceClass.PM25),
    "Pm1v": (CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, SensorDeviceClass.PM1),
}


# Doplňková čidla Te1–Te5, Vl1–Vl5, VlP, VlP2
def guess_unit_and_class(key: str):
    """Heuristika pro doplňková čidla."""
    k = key.lower()

    if k.startswith("te"):
        return UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE

    if k.startswith("vl"):
        return PERCENTAGE, SensorDeviceClass.HUMIDITY

    if "co2" in k:
        return CONCENTRATION_PARTS_PER_MILLION, SensorDeviceClass.CO2

    if "pm" in k:
        return CONCENTRATION_MICROGRAMS_PER_CUBIC_METER, SensorDeviceClass.PM25

    return None, None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
):
    """Set up PočasíMeteo sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = []

    # Projdeme všechna data z API
    for key, value in coordinator.data.items():
        # Přeskočíme metadata
        if key in ("raw", "meta", "station_name", "timestamp"):
            continue

        # SrazkyDen už není senzor → přeskočit
        if key == "SrazkyDen":
            continue

        # Pokud je hodnota None → nemá smysl vytvářet senzor
        if value is None:
            continue

        # Najdi definici senzoru
        if key in SENSOR_DEFINITIONS:
            unit, device_class = SENSOR_DEFINITIONS[key]
        else:
            unit, device_class = guess_unit_and_class(key)

        # Pokud ani heuristika nic nenašla → přeskočíme
        if unit is None and device_class is None:
            continue

        sensors.append(
            PocasimeteoSensor(
                coordinator=coordinator,
                entry=entry,
                key=key,
                unit=unit,
                device_class=device_class,
            )
        )

    async_add_entities(sensors, update_before_add=True)


class PocasimeteoSensor(CoordinatorEntity, SensorEntity):
    """Representation of a PočasíMeteo sensor."""

    def __init__(self, coordinator, entry, key, unit, device_class):
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._unit = unit
        self._attr_device_class = device_class

        station = coordinator.data.get("station_name", entry.title)
        self._attr_name = f"{station} {key}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        # Většina senzorů je měřená → measurement
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        return self.coordinator.data.get(self._key)

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def extra_state_attributes(self):
        # Speciální případ: směr větru
        if self._key == "VitrSmer":
            return {
                "mode": self.coordinator.data.get("VitrSmer_mode"),
                "avg": self.coordinator.data.get("VitrSmer_avg"),
                "variability": self.coordinator.data.get("VitrSmer_var"),
            }

        # Intenzita srážek má min/max/avg/mode/var
        if self._key == "SrazkyIntenzita":
            return {
                "min": self.coordinator.data.get("SrazkyIntenzita_min"),
                "max": self.coordinator.data.get("SrazkyIntenzita_max"),
                "avg": self.coordinator.data.get("SrazkyIntenzita_avg"),
                "mode": self.coordinator.data.get("SrazkyIntenzita_mode"),
                "variability": self.coordinator.data.get("SrazkyIntenzita_var"),
            }

        # Ostatní senzory mají min/max
        return {
            "min": self.coordinator.data.get(f"{self._key}_min"),
            "max": self.coordinator.data.get(f"{self._key}_max"),
        }
