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

# Recorder imports (modern HA versions)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States
from sqlalchemy import select

from .const import (
    DOMAIN,
    API_URL_BASE,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    CONF_SENSORS,
    SENSOR_DEFINITIONS,
    DEFAULT_SENSOR_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Main coordinator responsible for:
    - Fetching data from PočasíMeteo API
    - Importing 5-minute historical measurements into Home Assistant Recorder
    - Computing rolling 24-hour statistics (min/max/avg/mode/variance)
    - Normalizing API payload into HA sensor format
    - Injecting user-configured metadata (color, style, order, visibility)
    """

    # -------------------------------------------------------------------------
    # Recorder helpers
    # -------------------------------------------------------------------------

    async def _history_exists(self, entity_id: str, ts: datetime) -> bool:
        """Check if a historical state with the given timestamp already exists."""
        rec = get_instance(self.hass)
        with rec.get_session() as session:
            q = select(States).where(
                States.entity_id == entity_id,
                States.last_changed == ts
            )
            return session.execute(q).first() is not None

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        """Insert a historical state into Recorder DB."""
        rec = get_instance(self.hass)

        def _insert():
            with rec.get_session() as session:
                row = States(
                    entity_id=entity_id,
                    state=str(value),
                    last_changed=ts,
                    last_updated=ts,
                    attributes="{}"
                )
                session.add(row)
                session.commit()

        await rec.async_add_executor_job(_insert)

    # -------------------------------------------------------------------------
    # Import full 5-minute history from API
    # -------------------------------------------------------------------------

    async def _import_history(self, measurements: list[dict]):
        """
        Import complete 5-minute history into Recorder.
        Also rebuild rolling 24-hour statistics from scratch.
        """

        # Reset rolling statistics
        self._daily_stats = {
            "_date": date.today(),
            "vitr_smer_angles": [],
            "vitr_smer_sin_sum": 0.0,
            "vitr_smer_cos_sum": 0.0,
            "vitr_smer_count": 0
        }

        # Sort history by timestamp for correct rainfall intensity computation
        sorted_measurements = sorted(
            measurements,
            key=lambda m: datetime.fromisoformat(m["Datum"].replace("Z", "+00:00"))
        )

        previous_rain = None
        previous_ts = None

        # Compute rainfall intensity from history
        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            try:
                rain_total = float(m.get("SrazkyDen", 0))
            except Exception:
                rain_total = 0.0

            if previous_rain is not None:
                delta_rain = rain_total - previous_rain
                delta_time = (ts - previous_ts).total_seconds() / 3600.0

                if delta_rain > 0 and delta_time > 0:
                    intensity = round(delta_rain / delta_time, 2)
                else:
                    intensity = 0.0

                m["SrazkyIntenzita"] = intensity
            else:
                m["SrazkyIntenzita"] = 0.0

            previous_rain = rain_total
            previous_ts = ts

        # Store last computed intensity
        self._latest_rain_intensity = sorted_measurements[-1].get("SrazkyIntenzita", 0.0)

        # Insert history into Recorder and compute wind direction stats
        for m in measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))

            for key, value in m.items():
                if key in ("Datum", "LokalitaStanice", "DoplCidlaJson"):
                    continue

                entity_id = f"sensor.pocasimeteo_{key.lower()}"

                try:
                    v = float(value)
                except Exception:
                    v = value

                if v is None:
                    continue

                if not await self._history_exists(entity_id, ts):
                    await self._insert_history_point(entity_id, v, ts)

                # Wind direction circular statistics
                if key.lower() == "vitr_smer":
                    import math
                    angle = float(v)

                    self._daily_stats["vitr_smer_angles"].append(angle)

                    rad = math.radians(angle)
                    self._daily_stats["vitr_smer_sin_sum"] += math.sin(rad)
                    self._daily_stats["vitr_smer_cos_sum"] += math.cos(rad)
                    self._daily_stats["vitr_smer_count"] += 1

    # -------------------------------------------------------------------------
    # Coordinator initialization
    # -------------------------------------------------------------------------

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry

        # Load update interval from options or config
        update_interval_minutes = entry.options.get(
            CONF_UPDATE_INTERVAL,
            entry.data.get(CONF_UPDATE_INTERVAL, 5)
        )

        # Load sensor options (color, style, order, visibility)
        self._sensor_options = entry.options.get(CONF_SENSORS, DEFAULT_SENSOR_OPTIONS)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=update_interval_minutes),
        )

        self._daily_stats: dict[str, dict[str, float]] = {}

        # Legacy fields kept for compatibility
        self._last_rain_value: float | None = None
        self._last_rain_timestamp: datetime | None = None

        # Latest computed rainfall intensity
        self._latest_rain_intensity: float = 0.0

    # -------------------------------------------------------------------------
    # Main API update
    # -------------------------------------------------------------------------

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

        if isinstance(raw, dict) and "Zprava" in raw:
            raise UpdateFailed(f"PočasíMeteo API Error: {raw['Zprava']}")

        # Extract metadata and weather payload
        self.station_metadata = {}

        if isinstance(raw, list) and len(raw) > 0:
            meta_payload = raw[0]
            if isinstance(meta_payload, dict):
                self.station_metadata["station_name"] = meta_payload.get("LokalitaStanice")
                if "Webkamera" in meta_payload and isinstance(meta_payload["Webkamera"], dict):
                    self.station_metadata["webcamera_url"] = meta_payload["Webkamera"].get("UrlWebcam")

            if len(raw) > 1 and isinstance(raw[1], dict) and "Datum" in raw[1]:
                raw = raw[1]
            elif isinstance(raw[0], dict) and "Datum" in raw[0]:
                raw = raw[0]
            else:
                raise UpdateFailed("API response structure valid, but weather payload missing")

        if not isinstance(raw, dict):
            raise UpdateFailed("Invalid API response format")

        # ---------------------------------------------------------------------
        # Import 24h history BEFORE normalizing data
        # ---------------------------------------------------------------------

        history_payload = None

        if isinstance(raw.get("DoplCidlaJson"), dict):
            history_payload = raw["DoplCidlaJson"].get("Historie")

        if history_payload is None and isinstance(raw.get("Historie"), list):
            history_payload = raw["Historie"]

        if isinstance(history_payload, list) and len(history_payload) > 0:
            try:
                await self._import_history(history_payload)
            except Exception as hist_err:
                _LOGGER.warning(f"Import historie PočasíMeteo selhal: {hist_err}")

        # Inject latest computed rainfall intensity
        raw["SrazkyIntenzita"] = self._latest_rain_intensity

        # ---------------------------------------------------------------------
        # Normalize and compute statistics
        # ---------------------------------------------------------------------

        normalized = self._normalize_data(raw)
        self._update_daily_stats(normalized)

        # uložíme payload pro sensor.py a weather.py
        self.sensors_payload = normalized
        
        return normalized

    # -------------------------------------------------------------------------
    # Normalize API payload into HA sensor format
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        """
        Convert API fields into HA sensor format.
        Adds timestamp, numeric conversion, and user-configured metadata.
        """

        result: dict[str, dict] = {}
        timestamp_str = datetime.now().isoformat()

        # ---------------------------------------------------------------------
        # Known sensors from SENSOR_DEFINITIONS
        # ---------------------------------------------------------------------

        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"]
            value = raw.get(api_key)

            if value is None:
                continue

            # Convert numeric strings
            if isinstance(value, str):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass

            # Load user-configured options
            opts = self._sensor_options.get(sid, DEFAULT_SENSOR_OPTIONS.get(sid, {}))

            result[sid] = {
                "value": value,
                "type": meta["type"],
                "order": opts.get("order", meta["order"]),
                "graph_color": opts.get("color", meta["color"]),
                "graph_style": opts.get("style", "smooth"),
                "visible": opts.get("visible", True),
                "is_numeric": isinstance(value, (int, float)),
                "attributes": {"timestamp": timestamp_str},
            }

        # ---------------------------------------------------------------------
        # Dynamic sensors (unknown API keys)
        # ---------------------------------------------------------------------

        for api_key, value in raw.items():
            if value is None or api_key in ["Datum", "SrazkyDen"]:
                continue

            # Skip known sensors
            already_mapped = any(m["api_key"] == api_key for m in SENSOR_DEFINITIONS.values())
            if already_mapped:
                continue

            # Convert numeric strings
            if isinstance(value, str):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass

            from .const import get_dynamic_sensor_meta
            meta = get_dynamic_sensor_meta(api_key)
            sid = api_key.lower()

            # Load user-configured options or defaults
            opts = self._sensor_options.get(sid, {
                "order": meta["order"],
                "color": meta["color"],
                "style": "smooth",
                "visible": True,
            })

            result[sid] = {
                "value": value,
                "type": meta["type"],
                "order": opts["order"],
                "graph_color": opts["color"],
                "graph_style": opts["style"],
                "visible": opts["visible"],
                "is_numeric": isinstance(value, (int, float)),
                "attributes": {"timestamp": timestamp_str},
            }

        return result

    # -------------------------------------------------------------------------
    # Rolling 24h statistics
    # -------------------------------------------------------------------------

    def _update_daily_stats(self, data: dict[str, dict]):
        """
        Compute rolling 24-hour statistics:
        - min/max for all numeric sensors
        - avg/mode/variance for wind direction (circular statistics)
        """

        if "_date" not in self._daily_stats:
            self._daily_stats = {
                "_date": date.today(),
                "vitr_smer_angles": [],
                "vitr_smer_sin_sum": 0.0,
                "vitr_smer_cos_sum": 0.0,
                "vitr_smer_count": 0
            }

        for sid, payload in data.items():
            value = payload["value"]
            if not isinstance(value, (int, float)):
                continue

            # Min/max statistics
            stats = self._daily_stats.setdefault(sid, {"min": value, "max": value})
            stats["min"] = min(stats["min"], value)
            stats["max"] = max(stats["max"], value)

            payload["attributes"]["min"] = stats["min"]
            payload["attributes"]["max"] = stats["max"]

            # Wind direction circular statistics
            if sid == "vitr_smer":
                import math
                from collections import Counter

                angle = float(value)
                rad = math.radians(angle)

                self._daily_stats["vitr_smer_sin_sum"] += math.sin(rad)
                self._daily_stats["vitr_smer_cos_sum"] += math.cos(rad)
                self._daily_stats["vitr_smer_count"] += 1

                count = self._daily_stats["vitr_smer_count"]
                avg_sin = self._daily_stats["vitr_smer_sin_sum"] / count
                avg_cos = self._daily_stats["vitr_smer_cos_sum"] / count

                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos))
                if avg_deg < 0:
                    avg_deg += 360.0

                self._daily_stats["vitr_smer_angles"].append(angle)
                rounded = [round(a / 22.5) * 22.5 % 360 for a in self._daily_stats["vitr_smer_angles"]]
                mode_deg = Counter(rounded).most_common(1)[0][0]

                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                if 0.001 < r_vector < 1.0:
                    var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                else:
                    var_deg = 0.0

                payload["attributes"]["vitr_smer_avg"] = round(avg_deg, 1)
                payload["attributes"]["vitr_smer_mode"] = round(mode_deg, 1)
                payload["attributes"]["vitr_smer_var"] = round(min(var_deg, 180.0), 1)
