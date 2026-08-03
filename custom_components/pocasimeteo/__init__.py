"""PočasíMeteo integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CONF_UPDATE_INTERVAL,
    CONF_FORECAST_ENTITY_ID,
    CONF_SENSORS,
    DEFAULT_OPTIONS,
    DEFAULT_SENSOR_OPTIONS,
)
from .coordinator import PocasimeteoDataUpdateCoordinator
from .config_flow import PocasimeteoOptionsFlow

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["weather", "sensor"]


# ------------------------------------------------------------
# MIGRACE OPTIONS
# ------------------------------------------------------------

def _migrate_options(entry: ConfigEntry) -> dict:
    """Return migrated options in the new unified structure."""

    old_options = dict(entry.options) if entry.options else {}
    new_options = {}

    # 1) UPDATE INTERVAL
    new_options[CONF_UPDATE_INTERVAL] = old_options.get(
        CONF_UPDATE_INTERVAL,
        DEFAULT_OPTIONS[CONF_UPDATE_INTERVAL],
    )

    # 2) FORECAST ENTITY
    new_options[CONF_FORECAST_ENTITY_ID] = old_options.get(
        CONF_FORECAST_ENTITY_ID,
        DEFAULT_OPTIONS[CONF_FORECAST_ENTITY_ID],
    )

    # 3) SENSORS – hlavní část migrace
    sensors_opt = old_options.get(CONF_SENSORS)

    if sensors_opt is None:
        # Staré instalace → vytvoříme nový blok
        _LOGGER.debug("pocasimeteo: migrating sensors options → creating new block")
        new_options[CONF_SENSORS] = DEFAULT_SENSOR_OPTIONS.copy()

    else:
        # Nové instalace → doplníme chybějící senzory
        migrated = {}

        for sensor_id, defaults in DEFAULT_SENSOR_OPTIONS.items():
            if sensor_id in sensors_opt:
                # doplníme chybějící klíče
                migrated[sensor_id] = {
                    "order": sensors_opt[sensor_id].get("order", defaults["order"]),
                    "color": sensors_opt[sensor_id].get("color", defaults["color"]),
                    "style": sensors_opt[sensor_id].get("style", defaults["style"]),
                    "visible": sensors_opt[sensor_id].get("visible", True),
                }
            else:
                # senzor chybí → doplníme default
                migrated[sensor_id] = defaults.copy()

        new_options[CONF_SENSORS] = migrated

    return new_options


# ------------------------------------------------------------
# SETUP ENTRY
# ------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PočasíMeteo from a config entry."""

    _LOGGER.debug(
        "pocasimeteo: async_setup_entry start, entry_id=%s, options_before=%s",
        entry.entry_id,
        entry.options,
    )

    # MIGRACE OPTIONS
    migrated_options = _migrate_options(entry)

    if migrated_options != entry.options:
        _LOGGER.debug(
            "pocasimeteo: options migrated for entry_id=%s → %s",
            entry.entry_id,
            migrated_options,
        )
        hass.config_entries.async_update_entry(entry, options=migrated_options)

    # COORDINATOR
    coordinator = PocasimeteoDataUpdateCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to PočasíMeteo API: {err}") from err

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "entry": entry,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


# ------------------------------------------------------------
# UNLOAD / RELOAD
# ------------------------------------------------------------

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


# ------------------------------------------------------------
# OPTIONS FLOW
# ------------------------------------------------------------

async def async_get_options_flow(config_entry: ConfigEntry) -> PocasimeteoOptionsFlow:
    return PocasimeteoOptionsFlow(config_entry)
