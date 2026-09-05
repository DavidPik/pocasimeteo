"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["weather", "sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""

    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)
    _LOGGER.error("PM-TRACE: calling register_delayed_startup()")
    coordinator.register_delayed_startup()
    _LOGGER.error("PM-TRACE: register_delayed_startup() DONE")
    
    # 1. KROK: První refresh proběhne HNED. Stáhne se JSON, naplní se struktury 
    # v paměti a self._entity_id_map, aby platformy věděly, jaké entity mají vytvořit.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to PočasíMeteo API: {err}") from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "entry": entry,
    }

    # 2. KROK: Zavedení platforem weather a sensor do systému (nyní už mají data v paměti)
    _LOGGER.error("PM-TRACE: forwarding platforms")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.error("PM-TRACE: platforms forwarded")
    
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
