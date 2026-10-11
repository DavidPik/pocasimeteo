"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    DOMAIN,
    SERVICE_GET_HISTORY,
    HISTORY_ACTION_VERSION,
    CONF_DATABASE_PATH,
    SENSOR_DEFINITIONS,
)
from .database import PocasimeteoDatabase
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["weather", "sensor"]

_HISTORY_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Required("sensor_id"): str,
        vol.Required("start"): str,
        vol.Required("end"): str,
    },
    extra=vol.PREVENT_EXTRA,
)
_GET_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required("weather_entity_id"): str,
        vol.Required("requests"): vol.All(
            [_HISTORY_REQUEST_SCHEMA],
            vol.Length(min=1),
        ),
    },
    extra=vol.PREVENT_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the response action independently of loaded config entries."""

    async def async_get_history(call: ServiceCall) -> ServiceResponse:
        weather_entity_id = call.data["weather_entity_id"]
        entity_registry = er.async_get(hass)
        coordinator = None

        for entry_id, runtime_data in hass.data.get(DOMAIN, {}).items():
            entry = runtime_data.get("entry")
            candidate = runtime_data.get("coordinator")
            if entry is None or candidate is None:
                continue
            registered_entity_id = entity_registry.async_get_entity_id(
                "weather",
                DOMAIN,
                f"{entry_id}_weather",
            )
            if registered_entity_id == weather_entity_id:
                coordinator = candidate
                break

        if coordinator is None:
            raise ServiceValidationError(
                f"Weather entita {weather_entity_id!r} nepatří k načtené "
                "instanci integrace PočasíMeteo."
            )

        try:
            sensor_results = await coordinator.async_get_history(call.data["requests"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        return {
            "weather_entity_id": weather_entity_id,
            "version": HISTORY_ACTION_VERSION,
            "sensors": sensor_results,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_HISTORY,
        async_get_history,
        schema=_GET_HISTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""
    database_path = entry.data.get(CONF_DATABASE_PATH)
    if not isinstance(database_path, str) or not database_path.strip():
        raise ConfigEntryAuthFailed(
            "Není nastavena cesta k databázi PočasíMeteo."
        )

    database = PocasimeteoDatabase(hass, database_path=database_path)
    try:
        await database.async_open_and_validate()
        await database.async_store_dataset(
            station_id=entry.entry_id,
            station_name=entry.title,
            sensor_metadata=[
                {
                    "sensor_id": sensor_id,
                    "sensor_name": metadata["name"],
                    "sensor_type": metadata["sensor_type"],
                }
                for sensor_id, metadata in SENSOR_DEFINITIONS.items()
            ],
            measurements=[],
        )
    except Exception as err:
        _LOGGER.exception("Databáze PočasíMeteo není dostupná nebo platná")
        raise ConfigEntryAuthFailed(
            f"Databázi PočasíMeteo nelze otevřít nebo obsahuje neplatná data: {err}"
        ) from err

    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)
    coordinator._pocasimeteo_database = database
    coordinator.register_delayed_startup()
    
    # 1. KROK: První refresh proběhne HNED. Stáhne se JSON, naplní se struktury 
    # v paměti a self._entity_id_map, aby platformy věděly, jaké entity mají vytvořit.
    # try:
    #     await coordinator.async_config_entry_first_refresh()
    # except Exception as err:
    #     raise ConfigEntryNotReady(f"Cannot connect to PočasíMeteo API: {err}") from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator,"entry": entry}

    # 2. KROK: Zavedení platforem weather a sensor do systému (nyní už mají data v paměti)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Uvolnění integrace z paměti."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Správný asynchronní reload při změně options."""
    await hass.config_entries.async_reload(entry.entry_id)
