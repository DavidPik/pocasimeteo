"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import PocasimeteoDataUpdateCoordinator
from .options_flow import PocasimeteoOptionsFlow

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["weather", "sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """YAML setup is not supported for this integration."""
    _LOGGER.debug("pocasimeteo: async_setup called (YAML not supported)")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""
    _LOGGER.debug(
        "pocasimeteo: async_setup_entry start, title=%s, entry_id=%s, data=%s, options=%s",
        entry.title,
        entry.entry_id,
        entry.data,
        entry.options,
    )

    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("pocasimeteo: first refresh OK for entry_id=%s", entry.entry_id)
    except Exception as err:
        _LOGGER.error(
            "pocasimeteo: initial data fetch failed for entry_id=%s: %s",
            entry.entry_id,
            err,
        )
        # Continue setup even if API is temporarily unavailable

    hass.data.setdefault(DOMAIN, {})
    # Uložíme strukturu jako dict – je to robustnější pro budoucí rozšíření
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "entry": entry,
    }

    try:
        _LOGGER.debug(
            "pocasimeteo: forwarding entry_id=%s to platforms %s",
            entry.entry_id,
            PLATFORMS,
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug(
            "pocasimeteo: async_forward_entry_setups finished for entry_id=%s",
            entry.entry_id,
        )
    except Exception as err:
        _LOGGER.exception(
            "pocasimeteo: error while forwarding platforms for entry_id=%s: %s",
            entry.entry_id,
            err,
        )
        return False

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload PočasíMeteo config entry."""
    _LOGGER.debug(
        "pocasimeteo: async_unload_entry called for entry_id=%s",
        entry.entry_id,
    )
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        _LOGGER.debug(
            "pocasimeteo: async_unload_entry finished, entry_id=%s removed",
            entry.entry_id,
        )
    else:
        _LOGGER.error(
            "pocasimeteo: async_unload_entry failed for entry_id=%s",
            entry.entry_id,
        )
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle migration of old config entries."""
    _LOGGER.debug(
        "pocasimeteo: migrating entry %s (version %s)",
        entry.entry_id,
        entry.version,
    )
    # Zatím není potřeba měnit strukturu data/options – jen logujeme
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload PočasíMeteo config entry."""
    _LOGGER.debug(
        "pocasimeteo: async_reload_entry called for entry_id=%s",
        entry.entry_id,
    )
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_get_options_flow(config_entry: ConfigEntry) -> PocasimeteoOptionsFlow:
    """Return the options flow handler."""
    _LOGGER.debug(
        "pocasimeteo: async_get_options_flow called for entry_id=%s",
        config_entry.entry_id,
    )
    return PocasimeteoOptionsFlow(config_entry)
    
