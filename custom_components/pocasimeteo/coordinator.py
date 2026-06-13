"""Data update coordinator for PočasíMeteo."""

from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
import async_timeout

from homeassistant.helpers import aiohttp_client
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_STATION,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    API_URL_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator for fetching PočasíMeteo data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.station_name = entry.data[CONF_STATION]
        self.api_key = entry.data[CONF_API_KEY]

        interval_minutes = entry.data.get(CONF_UPDATE_INTERVAL, 5)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.station_name}",
            update_interval=timedelta(minutes=interval_minutes),
        )

        self.api_url = API_URL_TEMPLATE.format(api_key=self.api_key)

    async def _async_update_data(self):
        """Fetch data from PočasíMeteo API."""
        try:
            session = aiohttp_client.async_get_clientsession(self.hass)

            async with async_timeout.timeout(20):
                async with session.get(self.api_url, timeout=15) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"API returned HTTP {response.status}")

                    raw = await response.json()

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        # Normalize API formats
        if isinstance(raw, dict) and "data" in raw:
            records = raw["data"]
        elif isinstance(raw, dict) and raw.get("Zprava") == "Posilame data":
            records = [raw]
        elif isinstance(raw, list):
            records = raw
        else:
            raise UpdateFailed("Invalid API response format")

        if not records:
            raise UpdateFailed("API returned empty dataset")

        # Metadata vs measurements
        if records and (
            records[0].get("LokalitaStanice")
            or records[0].get("DoplCidlaJson")
        ):
            meta = records[0]
            measurements = records[1:]
        else:
            meta = None
            measurements = records

        if not measurements:
            raise UpdateFailed("No measurement records in API response")

        current = measurements[-1]

        if not isinstance(current, dict):
            raise UpdateFailed(f"Unexpected measurement format: {current!r}")

        # Helper: safe float conversion
        def _to_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        # Keys that should be numeric
        FLOAT_KEYS = {
            "TeplotaVnejsi",
            "VlhkostVnejsi",
            "Vitr",
            "VitrNarazy",
            "SrazkyDen",
            "TlakRel",
            "TeplotaVnitrni",
            "VlhkostVnitrni",
            "SlunZareni",
            "UVindex",
            "min_temp_24h",
            "max_temp_24h",
        }

        # Keys that should stay as string
        STRING_KEYS = {
            "VitrSmer",
        }

        # Base data dict
        data: dict[str, object] = {
            "station_name": self.station_name,
            "timestamp": current.get("Datum"),
            "meta": meta,
            "raw": current,
        }

        # Fill normalized values
        for key, value in current.items():
            if key in FLOAT_KEYS:
                data[key] = _to_float(value)
            elif key in STRING_KEYS:
                data[key] = value
            else:
                data[key] = value

        return data
