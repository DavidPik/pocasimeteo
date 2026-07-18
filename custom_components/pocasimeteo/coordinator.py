"""Data update coordinator for PočasíMeteo."""

from __future__ import annotations

import logging
from datetime import timedelta, datetime
import math

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

        # Helper: safe float conversion
        def _to_float(value):
            if value in (None, "", "-", "—"):
                return None
            try:
                return float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                return None

        # Keys that should be numeric (from API)
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
            "VitrSmer",
        }

        # Base data dict
        data: dict[str, object] = {
            "station_name": self.station_name,
            "timestamp": current.get("Datum"),
            "meta": meta,
            "measurements": measurements,  # needed for weather condition logic
        }

        # Fill normalized values
        for key, value in current.items():
            if key in FLOAT_KEYS:
                data[key] = _to_float(value)
            else:
                data[key] = value

        # ------------------------------------------------------------------
        # MIN/MAX výpočty pro všechny veličiny kromě VitrSmer
        # ------------------------------------------------------------------

        float_keys_for_minmax = FLOAT_KEYS - {"VitrSmer"}

        for key in float_keys_for_minmax:
            values = []
            for m in measurements:
                v = _to_float(m.get(key))
                if v is not None:
                    values.append(v)

            if values:
                data[f"{key}_min"] = min(values)
                data[f"{key}_max"] = max(values)
            else:
                data[f"{key}_min"] = None
                data[f"{key}_max"] = None

        # ------------------------------------------------------------------
        # AVG, MODE, VARIABILITY pro všechny numeric keys kromě VitrSmer
        # ------------------------------------------------------------------

        for key in float_keys_for_minmax:
            values = []
            for m in measurements:
                v = _to_float(m.get(key))
                if v is not None:
                    values.append(v)

            if values:
                data[f"{key}_avg"] = sum(values) / len(values)

                rounded = [round(v, 1) for v in values]
                data[f"{key}_mode"] = max(set(rounded), key=rounded.count)

                mean = data[f"{key}_avg"]
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                data[f"{key}_var"] = math.sqrt(variance)
            else:
                data[f"{key}_avg"] = None
                data[f"{key}_mode"] = None
                data[f"{key}_var"] = None

        # ------------------------------------------------------------------
        # Statistika směru větru (modus, cirkulární průměr, variabilita)
        # ------------------------------------------------------------------

        angles = []
        for m in measurements:
            v = _to_float(m.get("VitrSmer"))
            if v is not None:
                angles.append(v)

        if angles:
            bins = [0] * 12
            for a in angles:
                idx = int((a % 360) / 30)
                bins[idx] += 1
            mode_sector = bins.index(max(bins))
            data["VitrSmer_mode"] = mode_sector * 30

            angles_rad = [math.radians(a) for a in angles]
            avg_sin = sum(math.sin(a) for a in angles_rad) / len(angles_rad)
            avg_cos = sum(math.cos(a) for a in angles_rad) / len(angles_rad)
            avg_angle = math.degrees(math.atan2(avg_sin, avg_cos))
            if avg_angle < 0:
                avg_angle += 360
            data["VitrSmer_avg"] = avg_angle

            R = math.sqrt(avg_sin**2 + avg_cos**2)
            data["VitrSmer_var"] = 1 - R
        else:
            data["VitrSmer_mode"] = None
            data["VitrSmer_avg"] = None
            data["VitrSmer_var"] = None

        # ------------------------------------------------------------------
        # Výpočet okamžité intenzity srážek → SrazkyIntenzita
        # ------------------------------------------------------------------

        prev_total = self.hass.data.get(f"{DOMAIN}_prev_rain_total")
        prev_ts = self.hass.data.get(f"{DOMAIN}_prev_rain_ts")

        new_total = data.get("SrazkyDen")
        new_ts = data.get("timestamp")

        rain_intensity = None

        if prev_total is not None and new_total is not None and prev_ts and new_ts:
            try:
                dt = (datetime.fromisoformat(new_ts) - datetime.fromisoformat(prev_ts)).total_seconds() / 60
                if dt > 0:
                    intervals = max(1, round(dt / 5))
                    delta = new_total - prev_total

                    if delta < 0:
                        rain_intensity = 0
                    else:
                        rain_intensity = delta / intervals
            except Exception:
                rain_intensity = None

        data["SrazkyIntenzita"] = rain_intensity

        # Min/max pro SrazkyIntenzita
        if rain_intensity is not None:
            data["SrazkyIntenzita_min"] = rain_intensity
            data["SrazkyIntenzita_max"] = rain_intensity
        else:
            data["SrazkyIntenzita_min"] = None
            data["SrazkyIntenzita_max"] = None

        # uložit nový stav
        self.hass.data[f"{DOMAIN}_prev_rain_total"] = new_total
        self.hass.data[f"{DOMAIN}_prev_rain_ts"] = new_ts

        return data
