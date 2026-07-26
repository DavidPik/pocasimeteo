"""Data update coordinator for PočasíMeteo integration."""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta

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

        update_interval_minutes = entry.options.get(CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL, 5))

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=update_interval_minutes),
        )

        # Daily statistics storage (in-memory, reset at midnight)
        self._daily_stats: dict[str, dict[str, float]] = {}

    async def _async_update_data(self):
        """Fetch and normalize data from PočasíMeteo API."""
        api_key = self.entry.data[CONF_API_KEY]
        from .const import API_URL_BASE
        # Bezpečné složení URL pomocí f-stringu
        url = f"{API_URL_BASE}?KlicApi={api_key}"

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

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        """Convert API fields into internal structure dynamically based on API response."""
        result: dict[str, dict] = {}
        timestamp = datetime.now().isoformat()

        # Procházíme dynamicky VŠECHNA data, která přišla z API serveru
        for api_key, value in raw.items():
            if value is None:
                continue

            # Najdeme, zda pro tento API klíč máme definované interní ID v const.py
            internal_sid = None
            for sid, meta in SENSOR_DEFINITIONS.items():
                if meta.get("api_key") == api_key:
                    internal_sid = sid
                    break

            # Pokud klíč v const.py nemáme, vytvoříme dynamické ID podle názvu z API
            if internal_sid is None:
                internal_sid = api_key.lower()
                from .const import get_dynamic_sensor_meta
                meta = get_dynamic_sensor_meta(api_key)
            else:
                meta = SENSOR_DEFINITIONS[internal_sid]

            result[internal_sid] = {
                "value": value,
                "api_key": api_key,
                "type": meta.get("type", "secondary"),  # Uložíme typ do struktury
                "order": meta.get("order", 200),        # Uložíme pořadí do struktury
                "attributes": {
                    "timestamp": timestamp,
                },
            }

        return result

    def _update_daily_stats(self, data: dict[str, dict]):
        """Compute daily min/max values for each numeric sensor."""
        today = date.today()

        # Reset stats at midnight
        if "_date" not in self._daily_stats or self._daily_stats["_date"] != today:
            self._daily_stats = {"_date": today}

        for sid, payload in data.items():
            value = payload["value"]

            # Statistiky počítáme pouze pro číselné hodnoty (int, float)
            if not isinstance(value, (int, float)):
                continue

            stats = self._daily_stats.setdefault(sid, {"min": value, "max": value})

            if value < stats["min"]:
                stats["min"] = value
            if value > stats["max"]:
                stats["max"] = value

            # Přidáme statistiky do atributů entity
            payload["attributes"]["min"] = stats["min"]
            payload["attributes"]["max"] = stats["max"]
