"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

from .const import DOMAIN
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

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "entry": entry,
    }

    # 1. KROK: Nejprve bezpečně zavedeme platformy weather a sensor do systému.
    # Tím zajistíme, že entity v HA fyzicky existují dříve, než kdokoli začne sahat do DB.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 2. KROK: ARCHITEKTURNÍ ZMĚNA PROTI ZAMRZNUTÍ STARTU.
    # Integrace okamžitě uvolní startovací smyčku HA a vrátí True.
    # První ostré stažení dat z API a spuštění background workeru historie se odloží
    # na pozadí až do milisekundy, kdy jádro HA oznámí, že dokončilo bootování všech core služeb.
    async def _async_start_history_worker(_):
        _LOGGER.debug("Home Assistant je plně nastartován – spouštím první asynchronní refresh PočasíMeteo")
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:
            _LOGGER.error("Prvotní asynchronní refresh selhal: %s", err)

    # Zaregistrujeme jednorázový systémový listener na dokončení bootu HA
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _async_start_history_worker)
    
    # Registrace posluchače na změnu options (Lovelace konfigurace)
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
