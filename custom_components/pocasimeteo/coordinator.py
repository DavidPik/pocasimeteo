"""Data update coordinator for PočasíMeteo."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    API_URL_BASE,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    SENSOR_DEFINITIONS,
    get_dynamic_sensor_meta,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator pro načítání dat z PočasíMeteo API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        api_key = entry.data[CONF_API_KEY]
        options = entry.options or {}
        interval_min = options.get(CONF_UPDATE_INTERVAL, 5)

        self.api_key = api_key
        self.update_interval = timedelta(minutes=interval_min)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.entry_id})",
            update_interval=self.update_interval,
        )

        self.station_metadata: dict = {}
        self.sensors_payload: dict = {}

    async def _async_update_data(self):
        """Fetch data from API."""
        url = f"{API_URL_BASE}?KlicApi={self.api_key}"

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                data = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Error fetching PočasíMeteo data: {err}") from err

        # Očekáváme strukturu: { "station": {...}, "sensors": {...} }
        station = data.get("station", {})
        sensors = data.get("sensors", {})

        self.station_metadata = {
            "station_name": station.get("name"),
            "lokalita_stanice": station.get("location"),
            "timestamp": station.get("timestamp"),
            "webkamera_url": station.get("webcam_url"),
        }

        normalized = {}

        for api_key, value in sensors.items():
            sid = api_key.lower()
            meta = SENSOR_DEFINITIONS.get(sid, get_dynamic_sensor_meta(api_key))

            normalized[sid] = {
                "value": value,
                "meta": meta,
            }

        self.sensors_payload = normalized
        return normalized
