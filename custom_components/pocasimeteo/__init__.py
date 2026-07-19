"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import PocasimeteoDataUpdateCoordinator
from .options_flow import PocasimeteoOptionsFlow

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["weather", "sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """YAML setup is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""
    _LOGGER.debug("Setting up PočasíMeteo entry: %s", entry.title)

    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload PočasíMeteo config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data.pop(f"{DOMAIN}_prev_rain_total", None)
        hass.data.pop(f"{DOMAIN}_prev_rain_ts", None)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle migration of old config entries."""
    _LOGGER.debug("Migrating PočasíMeteo entry %s", entry.entry_id)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload PočasíMeteo config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_get_options_flow(config_entry):
    """Return the options flow handler."""
    return PocasimeteoOptionsFlow(config_entry)
