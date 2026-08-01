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
    SENSOR_DEFINITIONS,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Main coordinator responsible for:
    - Fetching data from PočasíMeteo API
    - Importing 5-minute historical measurements into Home Assistant Recorder
    - Computing rolling 24-hour statistics (min/max/avg/mode/variance)
    - Normalizing API payload into HA sensor format
    """

    # -------------------------------------------------------------------------
    # Recorder helpers
    # -------------------------------------------------------------------------

    async def _history_exists(self, entity_id: str, ts: datetime) -> bool:
        """
        Check if a historical state with the given timestamp already exists.
        Prevents duplicate inserts when API repeatedly sends the same history.
        """
        rec = get_instance(self.hass)
        with rec.get_session() as session:
            q = select(States).where(
                States.entity_id == entity_id,
                States.last_changed == ts
            )
            return session.execute(q).first() is not None

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        """
        Insert a historical state into Recorder DB.
        This allows HA graphs to show complete 24h history even after restart.
        """
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
        Import complete 5-minute history from API into Recorder.
        Also rebuild rolling 24-hour statistics from scratch.
        """

        # Reset rolling statistics — we rebuild them from imported history.
        # This ensures statistics match exactly the same 24h window as HA graphs.
        self._daily_stats = {
            "_date": date.today(),
            "vitr_smer_angles": [],
            "vitr_smer_sin_sum": 0.0,
            "vitr_smer_cos_sum": 0.0,
            "vitr_smer_count": 0
        }

        # --- Compute rainfall intensity directly from imported history ---
        # We sort history by timestamp ascending to compute correct deltas
        sorted_measurements = sorted(
            measurements,
            key=lambda m: datetime.fromisoformat(m["Datum"].replace("Z", "+00:00"))
        )

        previous_rain = None
        previous_ts = None

        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            try:
                rain_total = float(m.get("SrazkyDen", 0))
            except Exception:
                rain_total = 0.0

            # Compute intensity only if previous value exists
            if previous_rain is not None:
                delta_rain = rain_total - previous_rain
                delta_time = (ts - previous_ts).total_seconds() / 3600.0

                if delta_rain > 0 and delta_time > 0:
                    intensity = round(delta_rain / delta_time, 2)
                else:
                    intensity = 0.0

                # Store computed intensity back into the measurement
                m["SrazkyIntenzita"] = intensity
            else:
                # First point has no previous reference
                m["SrazkyIntenzita"] = 0.0

            previous_rain = rain_total
            previous_ts = ts

        for m in measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            # Convert ISO timestamp from API
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))

            # Process all measurement fields
            for key, value in m.items():
                if key in ("Datum", "LokalitaStanice", "DoplCidlaJson"):
                    continue

                entity_id = f"sensor.pocasimeteo_{key.lower()}"

                # Convert numeric values
                try:
                    v = float(value)
                except Exception:
                    v = value

                if v is None:
                    continue

                # Insert missing historical points into Recorder
                if not await self._history_exists(entity_id, ts):
                    await self._insert_history_point(entity_id, v, ts)

                # Extend rolling 24h statistics for wind direction
                if key.lower() == "vitr_smer":
                    import math
                    angle = float(v)

                    # Store angle for mode calculation
                    self._daily_stats["vitr_smer_angles"].append(angle)

                    # Add to vector sums for circular average
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
        update_interval_minutes = entry.options.get(CONF_UPDATE_INTERVAL, entry.data.get(CONF_UPDATE_INTERVAL, 5))

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=update_interval_minutes),
        )

        # Rolling 24h statistics storage
        self._daily_stats: dict[str, dict[str, float]] = {}

        # Rain intensity tracking
        self._last_rain_value: float | None = None
        self._last_rain_timestamp: datetime | None = None

    # -------------------------------------------------------------------------
    # Main API update
    # -------------------------------------------------------------------------

    async def _async_update_data(self):
        """
        Fetch and normalize data from PočasíMeteo API.
        Also import history and compute rolling statistics.
        """

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

        # API sometimes returns error messages inside JSON
        if isinstance(raw, dict) and "Zprava" in raw:
            raise UpdateFailed(f"PočasíMeteo API Error: {raw['Zprava']}")

        # Extract metadata and weather payload
        self.station_metadata = {}

        if isinstance(raw, list) and len(raw) > 0:
            meta_payload = raw[0]
            if isinstance(meta_payload, dict):
                self.station_metadata["lokalita"] = meta_payload.get("LokalitaStanice")
                if "Webkamera" in meta_payload and isinstance(meta_payload["Webkamera"], dict):
                    self.station_metadata["webcamera_url"] = meta_payload["Webkamera"].get("UrlWebcam")

            # Weather payload may be in raw[1] or raw[0]
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

        # ---------------------------------------------------------------------
        # Normalize and compute statistics
        # ---------------------------------------------------------------------

        normalized = self._normalize_data(raw)
        self._update_daily_stats(normalized)

        return normalized

    # -------------------------------------------------------------------------
    # Normalize API payload into HA sensor format
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        """
        Convert API fields into HA sensor format.
        Adds timestamp and numeric conversion.
        """
        result: dict[str, dict] = {}
        timestamp_str = datetime.now().isoformat()

        # Map known sensors
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

        # Map dynamic sensors
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

    # -------------------------------------------------------------------------
    # Rolling 24h statistics
    # -------------------------------------------------------------------------

    def _update_daily_stats(self, data: dict[str, dict]):
        """
        Compute rolling 24-hour statistics:
        - min/max for all numeric sensors
        - avg/mode/variance for wind direction (circular statistics)
        """

        # Ensure statistics structure exists
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

            # Rolling min/max
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

                # Update vector sums
                self._daily_stats["vitr_smer_sin_sum"] += math.sin(rad)
                self._daily_stats["vitr_smer_cos_sum"] += math.cos(rad)
                self._daily_stats["vitr_smer_count"] += 1

                count = self._daily_stats["vitr_smer_count"]
                avg_sin = self._daily_stats["vitr_smer_sin_sum"] / count
                avg_cos = self._daily_stats["vitr_smer_cos_sum"] / count

                # Circular average
                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos))
                if avg_deg < 0:
                    avg_deg += 360.0

                # Mode (rounded to nearest 22.5°)
                self._daily_stats["vitr_smer_angles"].append(angle)
                rounded = [round(a / 22.5) * 22.5 % 360 for a in self._daily_stats["vitr_smer_angles"]]
                mode_deg = Counter(rounded).most_common(1)[0][0]

                # Circular variance
                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                if 0.001 < r_vector < 1.0:
                    var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                else:
                    var_deg = 0.0

                payload["attributes"]["vitr_smer_avg"] = round(avg_deg, 1)
                payload["attributes"]["vitr_smer_mode"] = round(mode_deg, 1)
                payload["attributes"]["vitr_smer_var"] = round(min(var_deg, 180.0), 1)
