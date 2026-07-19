"""Data update coordinator for PočasíMeteo integration.

This module contains ONLY logic for:
- fetching data from PočasíMeteo API,
- mapping API fields to internal sensor IDs (defined in const.py),
- preparing a clean data structure for sensor/weather entities,
- computing lightweight daily statistics (min/max) as attributes.

All structural definitions (sensor metadata, API mapping, units, icons)
are centralized in const.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, date

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers import aiohttp_client

from .const import (
    DOMAIN,
    API_URL_TEMPLATE,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    SENSOR_DEFINITIONS,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator responsible for fetching and normalizing PočasíMeteo data."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry

        update_interval_minutes = entry.data.get(CONF_UPDATE_INTERVAL, 5)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,  # dynamic interval below
        )

        # Convert minutes to timedelta
        from datetime import timedelta

        self.update_interval = timedelta(minutes=update_interval_minutes)

        # Daily statistics storage (in-memory, reset at midnight)
        self._daily_stats: dict[str, dict[str, float]] = {}

    # ----------------------------------------------------------------------
    # Fetch data from API
    # ----------------------------------------------------------------------
    async def _async_update_data(self):
        """Fetch and normalize data from PočasíMeteo API."""
        api_key = self.entry.data[CONF_API_KEY]
        url = API_URL_TEMPLATE.format(api_key=api_key)

        try:
            session = aiohttp_client.async_get_clientsession(self.hass)
            async with async_timeout.timeout(20):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise UpdateFailed(f"HTTP {resp.status}")
                    raw = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"API request failed: {err}") from err

        # API returns either list or dict; normalize to dict
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if not isinstance(raw, dict):
            raise UpdateFailed("Invalid API response format")

        # Normalize data into internal structure
        normalized = self._normalize_data(raw)

        # Update daily statistics (min/max)
        self._update_daily_stats(normalized)

        return normalized

    # ----------------------------------------------------------------------
    # Normalize API payload into internal sensor structure
    # ----------------------------------------------------------------------
    def _normalize_data(self, raw: dict) -> dict[str, dict[str, float]]:
        """Convert API fields into internal sensor IDs defined in const.py.

        Output format:
            {
                "TeplotaVnejsi": {
                    "value": 23.4,
                    "attributes": {
                        "min": 12.1,
                        "max": 27.8,
                        "timestamp": "2026-07-19T19:58:00+02:00"
                    }
                },
                ...
            }
        """
        result: dict[str, dict] = {}
        timestamp = datetime.now().isoformat()

        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"]
            value = raw.get(api_key)

            if value is None:
                continue

            result[sid] = {
                "value": value,
                "attributes": {
                    "timestamp": timestamp,
                    # min/max added later by _update_daily_stats()
                },
            }

        return result

    # ----------------------------------------------------------------------
    # Daily min/max statistics (in-memory)
    # ----------------------------------------------------------------------
    def _update_daily_stats(self, data: dict[str, dict]):
        """Compute daily min/max values for each sensor.

        These statistics are stored only in memory and reset at midnight.
        They are exposed as attributes of each sensor entity.
        """
        today = date.today()

        # Reset stats at midnight
        if "_date" not in self._daily_stats or self._daily_stats["_date"] != today:
            self._daily_stats = {"_date": today}

        for sid, payload in data.items():
            value = payload["value"]

            stats = self._daily_stats.setdefault(sid, {"min": value, "max": value})

            if value < stats["min"]:
                stats["min"] = value
            if value > stats["max"]:
                stats["max"] = value

            # Attach stats to entity attributes
            payload["attributes"]["min"] = stats["min"]
            payload["attributes"]["max"] = stats["max"]
