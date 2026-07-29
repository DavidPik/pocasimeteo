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
    API_URL_BASE,
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

        self._daily_stats: dict[str, dict[str, float]] = {}
        self._last_rain_value: float | None = None
        self._last_rain_timestamp: datetime | None = None

    async def _async_update_data(self):
        """Fetch and normalize data from PočasíMeteo API."""
        api_key = self.entry.data[CONF_API_KEY]
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

        # OŠETŘENÍ RATE LIMITU: Pokud server posílá chybové hlášení, zastavíme parsování
        if isinstance(raw, dict) and "Zprava" in raw:
            raise UpdateFailed(f"PočasíMeteo API Error: {raw['Zprava']}")

        self.station_metadata = {}

        if isinstance(raw, list) and len(raw) > 0:
            meta_payload = raw[0]
            if isinstance(meta_payload, dict):
                self.station_metadata["lokalita"] = meta_payload.get("LokalitaStanice")
                if "Webkamera" in meta_payload and isinstance(meta_payload["Webkamera"], dict):
                    self.station_metadata["webcamera_url"] = meta_payload["Webkamera"].get("UrlWebcam")

            # Pokud limit sice neprošel přes dict, ale pole je zablokované chybovou zprávou
            if len(raw) > 1 and isinstance(raw[1], dict) and "Zprava" in raw[1]:
                raise UpdateFailed(f"PočasíMeteo API Error: {raw[1]['Zprava']}")

            if len(raw) > 1 and isinstance(raw[1], dict) and "Datum" in raw[1]:
                raw = raw[1]
            elif isinstance(raw[0], dict) and "Datum" in raw[0]:
                raw = raw[0]
            else:
                raise UpdateFailed("API response structure valid, but weather payload missing or rate limited")
        
        if not isinstance(raw, dict):
            raise UpdateFailed("Invalid API response format")

        # Pokud se v datech objeví textová chyba chránící frekvenci volání
        if "Zprava" in raw:
            raise UpdateFailed(f"PočasíMeteo API Limit: {raw['Zprava']}")

        try:
            self.station_metadata["srazky_den"] = float(raw.get("SrazkyDen", 0))
        except (ValueError, TypeError):
            self.station_metadata["srazky_den"] = 0.0

        # Spočítáme 5min intenzitu srážek
        rain_intensity = 0.0
        now = datetime.now()
        try:
            current_rain = float(raw.get("SrazkyDen", 0))
        except (ValueError, TypeError):
            current_rain = 0.0

        if self._last_rain_value is not None and self._last_rain_timestamp is not None:
            time_delta = now - self._last_rain_timestamp
            hours_passed = time_delta.total_seconds() / 3600.0

            if current_rain >= self._last_rain_value and hours_passed > 0.0027:
                rain_intensity = round((current_rain - self._last_rain_value) / hours_passed, 2)
            elif current_rain < self._last_rain_value and hours_passed > 0.0027:
                rain_intensity = round(current_rain / hours_passed, 2)
        
        self._last_rain_value = current_rain
        self._last_rain_timestamp = now
        raw["SrazkyIntenzita"] = rain_intensity

        normalized = self._normalize_data(raw)
        self._update_daily_stats(normalized)

        return normalized

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        """Map API fields directly to clean internal Czech IDs."""
        result: dict[str, dict] = {}
        timestamp_str = datetime.now().isoformat()

        # 1. Spárujeme známé senzory z const.py
        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"]
            value = raw.get(api_key)

            if value is None:
                continue

            if isinstance(value, str):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass

            result[sid] = {
                "value": value,
                "type": meta["type"],
                "order": meta["order"],
                "is_numeric": isinstance(value, (int, float)),
                "attributes": {"timestamp": timestamp_str},
            }

        # 2. Spárujeme případná nová dynamická čidla ze serveru
        for api_key, value in raw.items():
            if value is None or api_key in ["Datum", "SrazkyDen", "SrazkyIntenzita"]:
                continue

            already_mapped = any(m["api_key"] == api_key for m in SENSOR_DEFINITIONS.values())
            if already_mapped:
                continue

            if isinstance(value, str):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass

            from .const import get_dynamic_sensor_meta
            meta = get_dynamic_sensor_meta(api_key)
            sid = api_key.lower()

            result[sid] = {
                "value": value,
                "type": meta["type"],
                "order": meta["order"],
                "is_numeric": isinstance(value, (int, float)),
                "attributes": {"timestamp": timestamp_str},
            }

        return result

    def _update_daily_stats(self, data: dict[str, dict]):
        """Compute min/max values securely using Czech IDs."""
        today = date.today()

        if "_date" not in self._daily_stats or self._daily_stats["_date"] != today:
            self._daily_stats = {"_date": today}

        for sid, payload in data.items():
            value = payload["value"]
            if not isinstance(value, (int, float)):
                continue

            stats = self._daily_stats.setdefault(sid, {"min": value, "max": value})
            if value < stats["min"]:
                stats["min"] = value
            if value > stats["max"]:
                stats["max"] = value

            payload["attributes"]["min"] = stats["min"]
            payload["attributes"]["max"] = stats["max"]

            # ARCHITEKTONICKÁ ÚPRAVA: Přímý zápis denních statistik větru do senzoru vitr_smer
            if sid == "vitr_smer":
                payload["attributes"]["vitr_smer_avg"] = value
                payload["attributes"]["vitr_smer_mode"] = value
                payload["attributes"]["vitr_smer_var"] = 0.0
