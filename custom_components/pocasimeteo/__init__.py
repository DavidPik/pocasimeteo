"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_UPDATE_INTERVAL
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Seznam podporovaných platforem v integraci
PLATFORMS: list[str] = ["weather", "sensor"]


# ------------------------------------------------------------
# SETUP ENTRY
# ------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""

    # Vytvoření instance koordinátoru
    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)

    # Prvotní stažení dat z API s kontrolou dostupnosti
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to PočasíMeteo API: {err}") from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "entry": entry,
    }

    # Zavedení platforem weather a sensor do systému
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # ARCHITEKTURA HA: Registrace posluchače na změnu options.
    # Pokud uživatel v budoucnu klikne na 'Nastavit' a změní konfiguraci,
    # tento listener automaticky zachytí změnu a integraci bezpečně restartuje.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True


# ------------------------------------------------------------
# UNLOAD / RELOAD
# ------------------------------------------------------------

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Uvolnění integrace z paměti (např. při smazání nebo restartu)."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """ARCHITEKTURA HA: Správný asynchronní reload při změně options uživatelem."""
    await hass.config_entries.async_reload(entry.entry_id)
