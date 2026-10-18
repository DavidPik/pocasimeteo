"""Data update coordinator for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
import math
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import aiohttp_client

# Recorder components pro moderní DB schéma Home Assistenta
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta, StateAttributes
from homeassistant.util import dt as dt_util
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from zoneinfo import ZoneInfo

from .const import (
    DOMAIN,
    API_URL_BASE,
    CONF_API_KEY,
    CONF_DATABASE_PATH,
    CONF_UPDATE_INTERVAL,
    CONF_SENSORS,
    CONF_STATION,
    CONF_STATISTICS_INTERVAL,
    SENSOR_DEFINITIONS,
    DEFAULT_SENSOR_OPTIONS,
    DEFAULT_STATISTICS_INTERVAL,
    HISTORY_ACTION_MAX_DAYS,
    HISTORY_ACTION_RAW_MAX_HOURS,
    HISTORY_ACTION_RAW_INTERVAL_MINUTES,
    HISTORY_ACTION_AGGREGATE_INTERVAL_HOURS,
    ALLOWED_STATISTICS_INTERVALS,
    STATISTICS_TYPE_LINEAR,
    STATISTICS_TYPE_DIRECTIONAL,
    STATISTICS_TYPE_NONE,
    LINEAR_STATISTICS,
    DIRECTIONAL_STATISTICS,
    BACKEND_HISTORY_VERSION,
    BACKEND_HISTORY_MAX_HOURS,
    ATTR_BACKEND_HISTORY,
    ATTR_GRAPH_DATA,
    GRAPH_DATA_VERSION,
    ATTR_DATABASE_DIAGNOSTICS,
    get_dynamic_sensor_meta,
    API_TO_INTERNAL_MAPPING,
    CONF_STATION_TIMEZONE,
)
from .database import PocasimeteoDatabase

_LOGGER = logging.getLogger(__name__)

# =========================================================================
# ČISTÉ SYNCHRONNÍ DATABÁZOVÉ FUNKCE (DEFINOVANÉ MIMO TŘÍDY COORDINATORU)
# =========================================================================

def _insert_history_batch_sync_raw(session_factory, batch_points: list[dict]):
    """
    Kompletní hromadný zápis celé dávky v jednom synchronním DB vlákně.
    Tato verze již NEPRACUJE s API klíči – používá přímo entity_id, hodnotu a timestamp.
    Bezpečně pracuje v jedné transakci, bez vnořených session.begin() bloků.
    Zabrání neočekávanému autoflusha při SELECT a provede retry při sqlite zámku.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError
    from datetime import timezone as _timezone

    with session_factory() as session:
        meta_cache: dict[str, int] = {}
        attr_id: int | None = None

        # 1) Přednačtení sdíleného prázdného JSON atributu (StateAttributes)
        # SELECT bez autoflushu, aby se nepokoušel flushnout pending States
        with session.no_autoflush:
            attr_rows = session.execute(
                select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
            ).all()

        if attr_rows:
            attr_id = attr_rows[0][0].attributes_id
        else:
            attr_row = StateAttributes(shared_attrs="{}")
            session.add(attr_row)
            session.flush()
            attr_id = attr_row.attributes_id

        # 2) Zpracování jednotlivých bodů dávky
        for m in batch_points:
            ts = m.get("_computed_ts_utc")
            entity_id = m.get("entity_id")
            value = m.get("value")

            if not ts or not entity_id:
                continue

            # Převod času na float timestamp
            # Pokud má ts tzinfo, převedeme na UTC epoch, jinak použijeme naive timestamp
            try:
                if getattr(ts, "tzinfo", None) is not None:
                    utc_timestamp = ts.astimezone(_timezone.utc).timestamp()
                else:
                    # fallback: treat naive as UTC epoch
                    utc_timestamp = ts.replace(tzinfo=None).timestamp()
            except Exception:
                # Fallback: použijeme epoch podle naive datetu
                utc_timestamp = ts.replace(tzinfo=None).timestamp()

            # Konverze hodnoty na float nebo string
            if value in (None, "", " ", "N/A", "--"):
                continue

            try:
                v_float = float(value)
                if math.isnan(v_float):
                    continue
                formatted_state = f"{v_float:.2f}"
            except (ValueError, TypeError):
                formatted_state = str(value)

            # 3) Metadata (StatesMeta) – využití cache, jinak dohledání / vytvoření
            metadata_id = meta_cache.get(entity_id)
            if not metadata_id:
                # SELECT bez autoflushu, aby se nevyvolal flush pending States před dotazem
                with session.no_autoflush:
                    meta_rows = session.execute(
                        select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                    ).all()

                if meta_rows:
                    # pokud existuje více řádků, vezmeme první
                    meta_row = meta_rows[0][0]
                else:
                    meta_row = StatesMeta(entity_id=entity_id)
                    session.add(meta_row)
                    session.flush()

                metadata_id = meta_row.metadata_id
                meta_cache[entity_id] = metadata_id

            # 4) Vytvoření řádku States
            row = States(
                metadata_id=metadata_id,
                attributes_id=attr_id,
                state=formatted_state,
                last_changed_ts=utc_timestamp,
                last_updated_ts=utc_timestamp,
            )
            session.add(row)

        # 5) Commit celé dávky s retry a ošetřením zámku DB
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                session.commit()
                return
            except OperationalError as err:
                _LOGGER.error("PM-TRACE: HISTORY WRITE ERROR: %r (attempt %d/%d)", err, attempt + 1, max_attempts)
                session.rollback()
                if attempt < max_attempts - 1:
                    # krátké čekání před dalším pokusem (lineární backoff)
                    time.sleep(0.25 * (attempt + 1))
                    continue
                # poslední pokus selhal — logujeme a končíme
                _LOGGER.exception("PM-TRACE: HISTORY WRITE ERROR: final failure")
                return

# =========================================================================
# HLAVN9 KOORDINÁTOR JE ODPOV2DN7 ZA AKTUALIZACE DAT Z API pocasimeteo.cz
# =========================================================================

class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Koordinátor odpovědný za stahování dat, plnění mezer v historii databáze
    a výpočet statistik pro potřeby frontendové karty v jediném efektivním průchodu.
    """

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry

        update_interval_minutes = entry.options.get(
            CONF_UPDATE_INTERVAL,
            entry.data.get(CONF_UPDATE_INTERVAL, 5),
        )

        self._sensor_options = entry.options.get(CONF_SENSORS, DEFAULT_SENSOR_OPTIONS)
        self._statistics_interval = entry.options.get(
            CONF_STATISTICS_INTERVAL,
            DEFAULT_STATISTICS_INTERVAL,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,
        )

        # Rolling historie v paměti (pro min/max a kruhové statistiky směru větru)
        self._rolling_history: dict[str, list[tuple[datetime, float]]] = {}
        self._latest_rain_intensity: float = 0.0

        # Metadata stanice a payload senzorů
        self.station_metadata: dict = {}
        self.sensors_payload: dict[str, dict] = {}

        # Fronta pro doplnění historie do Recorderu – nyní již payload‑centrická
        self._history_queue: list[dict] = []
        self._history_task: asyncio.Task | None = None

        self._ha_started: bool = False

        # DIAGNOSTIKA – inicializace
        self._diag_queue_length: int = 0
        self._diag_worker_running: bool = False
        self._diag_missing_count: int = 0
        self._diag_last_batch_size: int = 0
        self._diag_last_write_ts: datetime | None = None

        # ARCHITEKTURA: Společný dynamický registr pro mapování API klíčů na reálná entity_id v HA
        # (používá se při normalizaci, ale worker už pracuje přímo s entity_id)
        self._entity_id_map: dict[str, str] = {}

        # ARCHITEKTURA: Dynamicky odvodíme základní identifikátory zařízení z konfigurační instance entry.
        self.station_metadata["device_info"] = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "PočasíMeteo",
        }

        # PRE-POPULATE REGISTRU: Okamžitě při startu provážeme pevně definované API klíče
        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"]
            key_lower = api_key.lower()
            internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)
            self._entity_id_map[api_key.lower()] = f"sensor.{station_prefix}_{internal_sid}"

    # -------------------------------------------------------------------------
    # TIMEZONE / TIMESTAMP HELPERS
    # -------------------------------------------------------------------------

    def _get_station_tz(self):
        """Return tzinfo for the station.

        Accept either:
        - IANA time zone name (e.g. "Europe/Prague")
        - fixed offset string like "+02:00" or "-05:30"
        - empty string or invalid → fall back to Home Assistant timezone
        """
        tz_name = self.entry.options.get(CONF_STATION_TIMEZONE) or ""
        if tz_name:
            tz_name = str(tz_name).strip()
            # fixed offset like +02:00 or -05:30
            if tz_name.startswith(("+", "-")):
                try:
                    sign = 1 if tz_name[0] == "+" else -1
                    h, m = tz_name[1:].split(":")
                    offset_min = sign * (int(h) * 60 + int(m))
                    return timezone(timedelta(minutes=offset_min))
                except Exception:
                    _LOGGER.warning("Invalid station timezone offset %r; falling back to HA timezone", tz_name)
            else:
                try:
                    return ZoneInfo(tz_name)
                except Exception:
                    _LOGGER.warning("Invalid station timezone name %r; falling back to HA timezone", tz_name)
        # fallback: Home Assistant timezone
        try:
            return dt_util.get_time_zone()
        except Exception:
            return timezone.utc

    def _parse_station_timestamp(self, value: str) -> datetime:
        """Parse API timestamp (wall time) into tz-aware datetime.

        API format is expected as 'YYYY-MM-DD HH:mm:ss' (no offset),
        treat as wall time in station timezone. If the string contains an
        offset or 'Z', respect it.
        """
        if not isinstance(value, str):
            raise ValueError("Timestamp must be a string")
        value = value.strip()
        # Try Home Assistant parser first (handles ISO with offset)
        parsed = dt_util.parse_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                tz = self._get_station_tz()
                return parsed.replace(tzinfo=tz)
            return parsed
        # Fallback: explicit "YYYY-MM-DD HH:MM:SS"
        try:
            naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            tz = self._get_station_tz()
            return naive.replace(tzinfo=tz)
        except Exception as err:
            raise ValueError(f"Cannot parse station timestamp: {value!r}") from err

    def _parse_history_wall_time(self, value: str) -> datetime:
        """Compatibility wrapper used by history APIs — returns tz-aware datetime."""
        return self._parse_station_timestamp(value)

    def _database_wall_time(self, value: str) -> datetime:
        """Parse a stored timestamp while preserving its displayed wall time as tz-aware datetime."""
        # Try parsing with dt_util first (handles ISO with offsets)
        parsed = dt_util.parse_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                # attach station tz
                return parsed.replace(tzinfo=self._get_station_tz())
            # normalize to station tz (so comparisons use station local time)
            return parsed.astimezone(self._get_station_tz())
        # if parse failed, raise
        raise ValueError(f"Invalid timestamp in DB: {value!r}")

    def _database_wall_timestamp(self, value: str) -> str:
        """Return stored timestamps as ISO strings in station local time (tz-aware)."""
        return self._database_wall_time(value).isoformat()

    # -------------------------------------------------------------------------
    # ASYNCHRONNÍ WRAPPERY PRO EXECUTOR JOBY (VOLAJÍ EXTERNÍ FUNKCE)
    # -------------------------------------------------------------------------

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        """Asynchronní fallback pro zápis osamocených živých stavů (např. intenzita srážek)."""
        fake_batch = [{"_computed_ts_utc": ts, "entity_id": entity_id, "value": value}]
        recorder = get_instance(self.hass)
        session_factory = recorder.get_session

        await recorder.async_add_executor_job(
            _insert_history_batch_sync_raw,
            session_factory,
            fake_batch,
        )

    # -------------------------------------------------------------------------
    # HLAVNÍ ASYNC UPDATE – STAHUJE JSON A VOLÁ NORMALIZACI + IMPORT HISTORIE
    # -------------------------------------------------------------------------

    async def _async_update_data(self):
@@
         # Historii zpracujeme pomocí již normalizovaných dat
         station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
         history_norm = normalized.get("history", [])
         await self._process_and_import_dataset(history_norm, station_prefix)
@@
         return self.sensors_payload
@@
     async def _async_refresh_daily_graph_data(self, normalized: dict) -> None:
         """Compute current local-calendar-day statistics from the integration DB."""
         database = getattr(self, "_pocasimeteo_database", None)
-        now_local = dt_util.now().replace(tzinfo=None)
-        day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
+        # Compute station-local "now" and calendar day boundaries
+        station_tz = self._get_station_tz()
+        now_station = dt_util.now().astimezone(station_tz)
+        day_start = now_station.replace(hour=0, minute=0, second=0, microsecond=0)
         latest_timestamp = normalized.get("current", {}).get("datum")
         if not latest_timestamp or database is None:
             self.station_metadata[ATTR_GRAPH_DATA] = {
                 "version": GRAPH_DATA_VERSION,
                 "period": "current_local_day",
-                "date": day_start.date().isoformat(),
-                "start": day_start.isoformat(),
-                "end": now_local.isoformat(),
+                "date": day_start.date().isoformat(),
+                "start": day_start.isoformat(),
+                "end": now_station.isoformat(),
                 "sensors": {},
             }
             return
 
         try:
-            latest = self._parse_history_wall_time(str(latest_timestamp))
+            latest = self._parse_station_timestamp(str(latest_timestamp))
         except ValueError:
-            latest = now_local
-        period_end = max(day_start, min(now_local, latest))
+            latest = now_station
+        period_end = max(day_start, min(now_station, latest.astimezone(station_tz)))
         query_start = day_start.isoformat()
-        query_end = day_start.replace(hour=23, minute=59, second=59).isoformat() + "\uffff"
+        query_end = day_start.replace(hour=23, minute=59, second=59).isoformat() + "\uffff"
         sensor_statistics: dict[str, dict] = {}
@@
             rows = await database.async_get_sensor_history(
                 self.entry.entry_id,
                 sensor_id,
                 query_start,
                 query_end,
             )
             values = []
             for timestamp, value in rows:
                 try:
-                    measured_at = self._database_wall_time(timestamp)
+                    measured_at = self._database_wall_time(timestamp)
                 except ValueError:
                     continue
-                if day_start <= measured_at <= period_end:
+                # measured_at is tz-aware in station tz; compare with station-local bounds
+                measured_local = measured_at.astimezone(station_tz)
+                if day_start <= measured_local <= period_end:
                     values.append(float(value))
@@
         self.station_metadata[ATTR_GRAPH_DATA] = {
             "version": GRAPH_DATA_VERSION,
             "period": "current_local_day",
-            "date": day_start.date().isoformat(),
-            "start": day_start.isoformat(),
-            "end": period_end.isoformat(),
+            "date": day_start.date().isoformat(),
+            "start": day_start.isoformat(),
+            "end": period_end.isoformat(),
             "sensors": sensor_statistics,
         }
@@
     async def _async_refresh_database_history(self, normalized: dict) -> None:
         """Načte měsíční historii a statistiky z vlastní DB pro weather entitu."""
         self.station_metadata.pop(ATTR_BACKEND_HISTORY, None)
         latest_timestamp = normalized.get("current", {}).get("datum")
         if not latest_timestamp:
             self.station_metadata.pop(ATTR_BACKEND_HISTORY, None)
             return
-
-        latest = datetime.fromisoformat(str(latest_timestamp).replace("Z", "+00:00"))
+        latest = self._parse_station_timestamp(str(latest_timestamp))
         history_start = latest - timedelta(hours=BACKEND_HISTORY_MAX_HOURS)
         try:
             statistics_hours = int(self._statistics_interval)
         except (TypeError, ValueError):
             statistics_hours = DEFAULT_STATISTICS_INTERVAL
         if statistics_hours not in ALLOWED_STATISTICS_INTERVALS:
             statistics_hours = DEFAULT_STATISTICS_INTERVAL
         statistics_start = latest - timedelta(hours=statistics_hours)
@@
             rows = await database.async_get_sensor_history(
                 self.entry.entry_id,
                 sensor_id,
                 history_start.isoformat(),
                 latest.isoformat(),
             )
             history = [
                 {"timestamp": timestamp, "value": value}
                 for timestamp, value in rows
             ]
             statistic_values = []
             for timestamp, value in rows:
                 try:
-                    measured_at = datetime.fromisoformat(
-                        timestamp.replace("Z", "+00:00")
-                    )
-                    if measured_at >= statistics_start:
-                        statistic_values.append(value)
+                    measured_at = self._database_wall_time(timestamp)
+                    if measured_at >= statistics_start:
+                        statistic_values.append(value)
                 except (TypeError, ValueError):
                     continue
@@
         self.station_metadata[ATTR_BACKEND_HISTORY] = {
             "version": BACKEND_HISTORY_VERSION,
             "period_hours": BACKEND_HISTORY_MAX_HOURS,
             "statistics_interval_hours": statistics_hours,
             "start": history_start.isoformat(),
             "end": latest.isoformat(),
             "sensors": sensor_history,
         }
@@
     def _aggregate_history_by_hour(
         self, sensor_id: str, rows: list[tuple[str, float]]
     ) -> list[dict]:
         """Return hourly means; partial first/last hours use their available samples."""
         metadata = SENSOR_DEFINITIONS.get(sensor_id)
         if metadata is None:
             metadata = get_dynamic_sensor_meta(sensor_id)

         hourly_values: dict[datetime, list[float]] = {}
         for timestamp, value in rows:
-            parsed = PocasimeteoDataUpdateCoordinator._database_wall_time(timestamp)
+            parsed = self._database_wall_time(timestamp)
             bucket = parsed.replace(minute=0, second=0, microsecond=0)
             hourly_values.setdefault(bucket, []).append(float(value))
@@
             points.append(
                 {
                     "timestamp": bucket.isoformat(),
                     "value": round(value, 2),
                 }
             )
         return points
@@
     async def async_get_history(self, requests: list[dict]) -> list[dict]:
@@
             start = self._parse_history_wall_time(request.get("start"))
             end = self._parse_history_wall_time(request.get("end"))
             if end <= start:
                 raise ValueError(f"Konec období senzoru {sensor_id!r} musí být po začátku.")
@@
             for sensor_id, start, end in parsed_requests:
@@
             query_start = start.strftime("%Y-%m-%dT00:00:00")
             query_end = end.strftime("%Y-%m-%dT23:59:59") + "\uffff"
@@
             rows = []
             for timestamp, value in stored_rows:
                 try:
                     measured_at = self._database_wall_time(timestamp)
                 except (TypeError, ValueError):
                     _LOGGER.warning(
                         "Přeskakuji neplatné časové razítko historie %r senzoru %s",
                         timestamp,
                         sensor_id,
                     )
                     continue
                 if start <= measured_at <= end:
                     rows.append((timestamp, float(value)))
             rows.sort(key=lambda row: self._database_wall_time(row[0]))
@@
                 history = [
                     {
                         "timestamp": self._database_wall_timestamp(timestamp),
                         "value": round(value, 2),
                     }
                     for timestamp, value in rows
                 ]
@@
                 history = self._aggregate_history_by_hour(sensor_id, rows)
@@
         await self._async_update_database_diagnostics(
             database,
             operation="history_action",
             started_at=query_started,
             result_count=sum(len(result["history"]) for result in results),
         )
         self.async_set_updated_data(self.data)
         return results
@@
     def _build_database_dataset(
         self, normalized: dict
     ) -> tuple[list[dict], list[dict]]:
@@
         for sample in samples:
             timestamp = sample.get("datum")
             if not timestamp:
                 continue

             for sensor_id in sensor_ids:
                 value = sample.get(sensor_id)
                 if value is None or isinstance(value, bool):
                     continue
                 try:
                     numeric_value = float(value)
                 except (TypeError, ValueError):
                     continue
                 if not math.isfinite(numeric_value):
                     continue

                 # apply unit conversions for DB storage
                 numeric_value = _convert_for_db(sensor_id, numeric_value)

-                measurements.append(
-                    {
-                        "sensor_id": sensor_id,
-                        "timestamp": timestamp,
-                        "value": numeric_value,
-                    }
-                )
+                # parse and store tz-aware ISO timestamp
+                try:
+                    parsed_ts = self._parse_station_timestamp(timestamp)
+                    ts_iso = parsed_ts.isoformat()
+                except Exception:
+                    # skip samples with invalid timestamps
+                    continue
+
+                measurements.append(
+                    {
+                        "sensor_id": sensor_id,
+                        "timestamp": ts_iso,
+                        "value": numeric_value,
+                    }
+                )
@@
     async def _process_and_import_dataset(self, history_norm, station_prefix):
         if not history_norm:
             return

         try:
-            sorted_measurements = sorted(
-                history_norm,
-                key=lambda m: datetime.fromisoformat(m["datum"]),
-            )
+            # Sort using parsed station timestamps (tz-aware)
+            sorted_measurements = sorted(
+                history_norm,
+                key=lambda m: self._parse_station_timestamp(m["datum"]),
+            )
         except Exception as err:
             _LOGGER.error("PM-TRACE: HISTORY SORT ERROR: %r", err, exc_info=True)
             return
@@
         for m in sorted_measurements:
             try:
-                ts = datetime.fromisoformat(m["datum"])
+                ts = self._parse_station_timestamp(m["datum"])
 
                 points = []
                 for sid, value in m.items():
                     if sid == "datum":
                         continue
@@
                 queue.append({
-                    "ts_utc": ts,
+                    "ts_utc": ts,
                     "points": points,
                 })
@@
         self._history_queue.extend(queue)
         self._diag_queue_length = len(self._history_queue)
@@
                 await recorder.async_add_executor_job(
                     _insert_history_batch_sync_raw,
                     session_factory,
                     batch_points,
                 )
@@
                 continue
@@
         _LOGGER.debug("Background worker úspěšně dokončil import chybějících mezer a uvolnil zámek")

    def register_delayed_startup(self):
@@
         if self.hass.is_running:
             self.hass.async_create_task(_play_delayed_history_worker(None))
@@
     def _query_history_values_sync(session_factory, entity_id: str, start_ts: float) -> list[float]:
@@
                 rows = session.execute(
                     select(States.state)
                     .select_from(States)
                     .join(StatesMeta, States.metadata_id == StatesMeta.metadata_id)
                     .where(StatesMeta.entity_id == entity_id)
                     .where(States.last_updated_ts >= start_ts)
                     .order_by(States.last_updated_ts.asc())
                 ).all()
@@
                 return values
@@
         # HLAVNÍ CYKLUS — iterujeme přes všechny senzory v sensors_payload
         for sid, payload in data.items():
@@
             if internal_sid == "srazky_intenzita":
@@
                 _LOGGER.debug(
@@
             if not values:
@@
                 continue
@@
                 self.station_metadata["sensor_stats"][sid] = {
                     "stats_min": payload["value"],
                     "stats_max": payload["value"],
                 }
                 continue
@@
                 self.station_metadata["sensor_stats"][sid] = {
                     "stats_min": round(min(values), 2),
                     "stats_max": round(max(values), 2),
                 }
@@
     def _normalize_data(self, raw_json):
@@
-        current = {
-            "datum": current_raw.get("Datum"),
+        current = {
+            "datum": current_raw.get("Datum"),
             "teplota_vnejsi": self._to_float(current_raw.get("TeplotaVnejsi")),
@@
         }
@@
-        if len(history_raw) >= 1 and current_raw.get("SrazkyDen") is not None:
+        if len(history_raw) >= 1 and current_raw.get("SrazkyDen") is not None:
             try:
                 rain_now = float(current_raw.get("SrazkyDen", 0))
                 prev = history_raw[0]
                 rain_prev = float(prev.get("SrazkyDen", 0))
 
-                ts_now = dt_util.parse_datetime(current_raw["Datum"].replace("Z", ""))
-                ts_prev = dt_util.parse_datetime(prev["Datum"].replace("Z", ""))
+                ts_now = self._parse_station_timestamp(current_raw["Datum"]) if current_raw.get("Datum") else None
+                ts_prev = self._parse_station_timestamp(prev["Datum"]) if prev.get("Datum") else None
 
                 delta_rain = rain_now - rain_prev
-                delta_hours = (ts_now - ts_prev).total_seconds() / 3600.0
+                delta_hours = (ts_now - ts_prev).total_seconds() / 3600.0
@@
-        history_raw_sorted = sorted(
-            history_raw,
-            key=lambda item: datetime.fromisoformat(item["Datum"].replace("Z", "")),
-        )
+        history_raw_sorted = sorted(
+            history_raw,
+            key=lambda item: self._parse_station_timestamp(item["Datum"]),
+        )
@@
-                    ts_now = dt_util.parse_datetime(h["datum"].replace("Z", ""))
-                    ts_prev = dt_util.parse_datetime(prev_h["datum"].replace("Z", ""))
+                    ts_now = self._parse_station_timestamp(h["datum"]) if h.get("datum") else None
+                    ts_prev = self._parse_station_timestamp(prev_h["datum"]) if prev_h.get("datum") else None
@@
         # --- 5) Naplnění sensors_payload (pro sensor.py) ---
         sensors_payload = {}
         station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")

         for sid, meta in SENSOR_DEFINITIONS.items():
@@
-            sensors_payload[internal_sid] = {
-                "value": value,
-                "attributes": {
-                    "timestamp": current.get("datum"),
-                    "vitr_smer_avg": None,
-                    "vitr_smer_mode": None,
-                    "vitr_smer_var": None,
-                },
-            }
+            # ensure attributes timestamp is tz-aware ISO string
+            try:
+                api_ts = self._parse_station_timestamp(current.get("datum")) if current.get("datum") else None
+                api_ts_iso = api_ts.isoformat() if api_ts else None
+            except Exception:
+                api_ts_iso = None
+
+            sensors_payload[internal_sid] = {
+                "value": value,
+                "attributes": {
+                    "timestamp": api_ts_iso,
+                    "vitr_smer_avg": None,
+                    "vitr_smer_mode": None,
+                    "vitr_smer_var": None,
+                },
+            }
@@
         # --- 6) Přidání syntetického senzoru do sensors_payload ---
         sensors_payload["srazky_intenzita"] = {
             "value": current["srazky_intenzita"],
             "attributes": {
-                "timestamp": current.get("datum"),
+                "timestamp": api_ts_iso,
             },
         }
@@
-        api_ts_raw = current.get("datum")
-        if api_ts_raw:
-            try:
-                self.station_metadata["api_timestamp"] = dt_util.parse_datetime(
-                    api_ts_raw.replace("Z", "")
-                ).isoformat()
-            except Exception:
-                self.station_metadata["api_timestamp"] = dt_util.now().isoformat()
-        else:
-            self.station_metadata["api_timestamp"] = None
+        api_ts_raw = current.get("datum")
+        if api_ts_raw:
+            try:
+                api_ts = self._parse_station_timestamp(api_ts_raw)
+                self.station_metadata["api_timestamp"] = api_ts.isoformat()
+            except Exception:
+                self.station_metadata["api_timestamp"] = dt_util.now().isoformat()
+        else:
+            self.station_metadata["api_timestamp"] = None
@@
         # --- 8) Vrácení kompletního normalizovaného datasetu ---
         return {
             "metadata": metadata_raw,
             "current": current,
             "history": history,
             "sensors": sensors_payload,
         }
@@
     def _to_float(self, value):
         """Bezpečný převod na float."""
         try:
             if value in (None, "", " ", "N/A", "--"):
                 return None
             return float(value)
         except Exception:
             return None
@@
     def _to_int(self, value):
         """Bezpečný převod na int."""
         try:
             if value in (None, "", " ", "N/A", "--"):
                 return None
             return int(float(value))
         except Exception:
             return None
