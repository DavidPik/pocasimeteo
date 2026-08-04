"""Sensor platform for PočasíMeteo."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PočasíMeteo sensors from config entry."""
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: PocasimeteoDataUpdateCoordinator = store["coordinator"]

    # Sledujeme již vytvořená unikátní ID entit, abychom zamezili duplicitám
    known_entities: set[str] = set()

    @callback
    def async_add_new_sensors():
        """Vnitřní funkce pro dynamické přidání nově objevených čidel z API."""
        new_entities: list[PočasíMeteoSensor] = []
        
        for sensor_id, payload in coordinator.sensors_payload.items():
            unique_id = f"{entry.entry_id}_{sensor_id}"
            if unique_id in known_entities:
                continue
                
            meta = payload["meta"]
            new_entities.append(PočasíMeteoSensor(coordinator, entry, sensor_id, meta))
            known_entities.add(unique_id)

        if new_entities:
            async_add_entities(new_entities)

    # Prvotní registrace entit při startu
    async_add_new_sensors()

    # ODCHYLKA: Registrujeme posluchač na koordinátor. Pokud se při další aktualizaci dat
    # objeví v JSONu z API nové čidlo, koordinátor vyvolá update a my ho za běhu přidáme do HA.
    entry.async_on_unload(
        coordinator.async_add_listener(async_add_new_sensors)
    )


class PočasíMeteoSensor(CoordinatorEntity[PocasimeteoDataUpdateCoordinator], SensorEntity):
    """Reprezentace jednoho senzoru PočasíMeteo provázaného s koordinátorem."""

    def __init__(
        self,
        coordinator: PocasimeteoDataUpdateCoordinator,
        entry: ConfigEntry,
        sensor_id: str,
        meta: dict,
    ) -> None:
        """Inicializace senzoru a nastavení základních vlastností."""
        super().__init__(coordinator)
        self._sensor_id = sensor_id
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_{sensor_id}"
        self._attr_name = meta.get("name", sensor_id)
        self._attr_icon = meta.get("icon")
        self._attr_native_unit_of_measurement = meta.get("unit")
        self._attr_device_class = meta.get("device_class")
        self._attr_state_class = meta.get("state_class")

        # Všechny senzory seskupíme pod jedno fyzické zařízení (meteo stanici) v HA
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.station_metadata.get("station_name") or "PočasíMeteo",
            manufacturer="PočasíMeteo",
            model="Meteostanice",
        )

    @property
    def native_value(self):
        """Vrací aktuální hodnotu senzoru přímo z paměti koordinátoru."""
        payload = self.coordinator.sensors_payload.get(self._sensor_id)
        if not payload:
            return None
        return payload.get("value")
        
    @property
    def extra_state_attributes(self):
        """Vrací doplňkové atributy pro frontendovou kartu PočasíMeteo."""
        payload = self.coordinator.sensors_payload.get(self._sensor_id)
        if not payload:
            return {}

        meta = payload["meta"]
        attrs = payload.get("attributes", {})

        # ARCHITEKTURA FRONTENDU: Tyto atributy jsou klíčové pro frontendovou kartu.
        # Karta si z každého senzoru vytáhne definovanou barvu, pořadí a styl grafu,
        # což umožňuje kompletní správu designu dashboardu přímo z nastavení integrace.
        return {
            "graph_color": meta.get("color"),
            "graph_style": meta.get("style"),
            "order": meta.get("order"),
            "visible": meta.get("visible"),
            **attrs,  # Zde se propisují vypočtené 24h statistiky (min, max, vitr_smer_avg atd.)
        }
