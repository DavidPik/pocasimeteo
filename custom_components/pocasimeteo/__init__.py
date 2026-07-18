"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import PocasimeteoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["weather", "sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """YAML setup is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""
    _LOGGER.debug("Setting up PočasíMeteo entry: %s", entry.title)

    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)

    # First data refresh
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload PočasíMeteo config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove coordinator
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # Remove rain intensity cache
        hass.data.pop(f"{DOMAIN}_prev_rain_total", None)
        hass.data.pop(f"{DOMAIN}_prev_rain_ts", None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload PočasíMeteo config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
