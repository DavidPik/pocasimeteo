"""Data update coordinator for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
import math
import time
from collections import Counter
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import aiohttp_client

# Recorder components pro moderní DB schéma Home Assistenta
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta, StateAttributes
from homeassistant.util import dt as dt_util
from sqlalchemy import select

from .const import (
    DOMAIN,
    API_URL_BASE,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    CONF_SENSORS,
    CONF_STATION,
    CONF_STATISTICS_INTERVAL,
    SENSOR_DEFINITIONS,
    DEFAULT_SENSOR_OPTIONS,
    DEFAULT_STATISTICS_INTERVAL,
    get_dynamic_sensor_meta,
    API_TO_INTERNAL_MAPPING,
)

_LOGGER = logging.getLogger(__name__)

# =========================================================================
# ČISTÉ SYNCHRONNÍ DATABÁZOVÉ FUNKCE (DEFINOVANÉ MIMO TŘÍDU COORDINATORU)
# =========================================================================

def _query_recorder_history_sync(session_pool, target_entity_id: str, start_timestamp: float) -> list[float]:
    """Čistě synchronní I/O dotaz do Recorderu, spuštěný odděleně v thread poolu."""
    # Získání instance Recorderu přímo z kontextu běžícího vlákna
    with session_pool() as session:
        rows = session.execute(
            select(States.state)
            .where(
                States.entity_id == target_entity_id,
                States.last_changed_ts >= start_timestamp,
            )
        ).all()
        
        values = []
        for row in rows:
            state_val = row if isinstance(row, tuple) else row
            if state_val in (None, "", "unknown", "unavailable"):
                continue
            try:
                v = float(state_val)
                if not math.isnan(v):
                    values.append(v)
            except (ValueError, TypeError):
                continue
        return values

def _query_existing_timestamps_sync(session_pool: HomeAssistant, sample_entity: str, processed_timestamps: set[float]) -> set[float]:
    """Hromadně ověří existenci celé sady timestampů v DB v synchronním executoru."""
    with session_pool() as session:
        rows = session.execute(
            select(States.last_changed_ts)
            .where(
                States.entity_id == sample_entity,
                States.last_changed_ts.in_(processed_timestamps)
            )
        ).all()
        return {float(row[0]) for row in rows if row and row[0] is not None}

def _insert_history_batch_sync_raw(session_pool, entity_id_map: dict[str, str], batch_measurements: list[dict], allowed_api_keys: set[str]):
    """Kompletní hromadný zápis celé dávky v jednom synchronním DB vlákně bez úniku do asynchronního jádra."""
    with session_pool() as session:
        meta_cache: dict[str, int] = {}
        attr_id = None

        for m in batch_measurements:
            ts = m.get("_computed_ts_utc")
            if not ts:
                continue
            utc_timestamp = ts.replace(tzinfo=None).timestamp()

            for api_key, value in m.items():
                if api_key in ("Datum", "LokalitaStanice", "DoplCidlaJson", "_computed_ts_utc"):
                    continue
                if api_key not in allowed_api_keys:
                    continue
                if value in (None, "", " ", "N/A", "--"):
                    continue

                entity_id = entity_id_map.get(api_key)
                if not entity_id:
                    continue

                try:
                    v_float = float(value)
                    if math.isnan(v_float):
                        continue
                    formatted_state = f"{v_float:.1f}"
                except (ValueError, TypeError):
                    formatted_state = str(value)

                metadata_id = meta_cache.get(entity_id)
                if not metadata_id:
                    meta_row = session.execute(
                        select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                    ).scalar_one_or_none()
                    if not meta_row:
                        meta_row = StatesMeta(entity_id=entity_id)
                        session.add(meta_row)
                        session.flush()
                    metadata_id = meta_row.metadata_id
                    meta_cache[entity_id] = metadata_id

                if attr_id is None:
                    attr_row = session.execute(
                        select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
                    ).scalar_one_or_none()
                    if not attr_row:
                        attr_row = StateAttributes(shared_attrs="{}")
                        session.add(attr_row)
                        session.flush()
                    attr_id = attr_row.attributes_id

                row = States(
                    entity_id=entity_id,
                    metadata_id=metadata_id,
                    attributes_id=attr_id,
                    state=formatted_state,
                    last_changed_ts=utc_timestamp,
                    last_updated_ts=utc_timestamp,
                    last_changed=ts,
                    last_updated=ts,
                )
                session.add(row)
        session.commit()

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
            update_interval=timedelta(minutes=update_interval_minutes),
        )

        self._rolling_history: dict[str, list[tuple[datetime, float]]] = {}
        self._latest_rain_intensity: float = 0.0
        self.station_metadata = {}
        self.sensors_payload = {}

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
        self._entity_id_map: dict[str, str] = {}
        
        # PRE-POPULATE REGISTRU: Okamžitě při startu provážeme pevně definované API klíče
        # To zaručí, že background worker má mapování od první milisekundy běhu HA.
        station_prefix = self.entry.title.lower().strip().replace(" ", "_")
        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"]
            key_lower = api_key.lower()
            internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)
            self._entity_id_map[api_key] = f"sensor.{station_prefix}_{internal_sid}"

    # -------------------------------------------------------------------------
    # ASYNCHRONNÍ WRAPPERY PRO EXECUTOR JOBY (VOLAJÍ EXTERNÍ FUNKCE)
    # -------------------------------------------------------------------------

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        """Asynchronní fallback pro zápis osamocených živých stavů (např. intenzita srážek)."""
        fake_batch = [{"_computed_ts_utc": ts, entity_id: value}]
        allowed = {entity_id}
        temp_map = {entity_id: entity_id}
        await self.hass.async_add_executor_job(
            _insert_history_batch_sync_raw, get_instance(self.hass).get_session, temp_map, fake_batch, allowed
        )

    # -------------------------------------------------------------------------
    # UNIFIKOVANÉ ZPRACOVÁNÍ DATASETU (LOGIKA V JEDNOM PRŮCHODU)
    # -------------------------------------------------------------------------

    async def _process_and_import_dataset(self, measurements: list[dict], station_prefix: str):
        """
        Sloučená logika úloh 1, 2, 3 a 4 do jednoho jediného efektivního průchodu.
        Spočítá statistiky z JSONu, zkontroluje DB a naplní frontu pouze chybějícími body.
        """
        if not measurements:
            return None

        # Seřazení od nejstaršího po nejnovější pro správnou srážkovou intenzitu
        sorted_measurements = sorted(
            measurements,
            key=lambda m: datetime.fromisoformat(m["Datum"]),
        )

        extracted_stats: dict[str, list[float]] = {}
        previous_rain = None
        previous_ts = None
        prepared_history_points = []
        processed_timestamps = set()

        live_boundary = time.time() - 600
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)

        # A. HLAVNÍ JEDINÝ CYKLUS NAD DATASETEM
        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            try:
                naive_local = datetime.fromisoformat(ts_raw)
                localized_dt = naive_local.replace(tzinfo=local_tz)
                ts_utc_naive = dt_util.as_utc(localized_dt).replace(tzinfo=None)
                utc_timestamp = ts_utc_naive.timestamp()
            except Exception as e:
                _LOGGER.error("Chyba konverzi času u bodu %s: %s", ts_raw, e)
                continue

            # Výpočet intenzity srážek
            try:
                rain_total = float(m.get("SrazkyDen", 0))
            except Exception:
                rain_total = 0.0

            if previous_rain is not None:
                delta_rain = rain_total - previous_rain
                delta_time = (ts_utc_naive - previous_ts).total_seconds() / 3600.0
                intensity = round(delta_rain / delta_time, 2) if (delta_rain > 0 and delta_time > 0) else 0.0
                m["SrazkyIntenzita"] = intensity
            else:
                m["SrazkyIntenzita"] = 0.0

            previous_rain = rain_total
            previous_ts = ts_utc_naive

            # Sběr dat pro rolling statistiky a filtrace klíčů
            for api_key, value in m.items():
                if api_key in ("Datum", "LokalitaStanice", "DoplCidlaJson"):
                    continue
                if value in (None, "", " ", "N/A", "--"):
                    continue
                try:
                    v_float = float(value)
                    if not math.isnan(v_float):
                        internal_sid = API_TO_INTERNAL_MAPPING.get(api_key.lower(), api_key.lower())
                        extracted_stats.setdefault(internal_sid, []).append(v_float)
                except Exception:
                    continue

            # Příprava bodu pro historii (pokud je starší než 10 minut)
            if utc_timestamp <= live_boundary:
                prepared_history_points.append({
                    "ts_utc": ts_utc_naive,
                    "ts_float": utc_timestamp,
                    "data": m
                })
                processed_timestamps.add(utc_timestamp)

        if sorted_measurements:
            self._latest_rain_intensity = sorted_measurements[-1].get("SrazkyIntenzita", 0.0)

        # B. JEDEN HROMADNÝ DOTAZ DO DB (ODSTRANĚNÍ DUPLICIT)
        if prepared_history_points and self._ha_started:
            sample_entity = f"sensor.{station_prefix}_teplota_vnejsi"
            try:
                existing_timestamps = await self.hass.async_add_executor_job(
                    self._query_existing_timestamps,
                    get_instance(self.hass).get_session,
                    sample_entity,
                    processed_timestamps
                )
            except Exception as db_err:
                _LOGGER.warning("Hromadný dotaz na existenci historie selhal: %s", db_err)
                existing_timestamps = set()

            final_queue = [
                pt for pt in prepared_history_points 
                if pt["ts_float"] not in existing_timestamps
            ]
        else:
            final_queue = []

        # C. SPUŠTĚNÍ WORKERU (POUZE POKUD MÁME CHYBĚJÍCÍ DATA)
        if final_queue:
            worker_payload = []
            for item in final_queue:
                row_data = dict(item["data"])
                row_data["_computed_ts_utc"] = item["ts_utc"]
                worker_payload.append(row_data)

            if self._history_queue:
                self._history_queue.extend(worker_payload)
            else:
                self._history_queue = worker_payload

            self._diag_queue_length = len(self._history_queue)

            if self._ha_started and (self._history_task is None or self._history_task.done()):
                _LOGGER.debug("Spouštím background worker pro doplnění mezer (velikost: %s)", self._diag_queue_length)
                self._history_task = self.hass.async_create_task(self._history_worker())
        else:
            _LOGGER.debug("Všechna historická data z JSONu již v DB existují. Vynechávám spuštění workeru.")

        return extracted_stats

    # -------------------------------------------------------------------------
    # HISTORICKÝ BACKGROUND WORKER & ODLOŽENÝ START
    # -------------------------------------------------------------------------

    async def _history_worker(self):
        """Background worker, který bezpečně a hromadně deleguje zápis dávek do executoru."""
        station_prefix = self.entry.title.lower().strip().replace(" ", "_")
        batch_size = 60  
        pause = 0.2  

        self._diag_worker_running = True

        allowed_api_keys = {meta["api_key"] for meta in SENSOR_DEFINITIONS.values()}
        allowed_api_keys.update(["TeplotaVnejsi", "TeplotaVnitrni", "VlhkostVnejsi", "VlhkostVnitrni", "SrazkyDen", "SlunZareni", "UVindex", "Vitr", "VitrNarazy", "VitrSmer", "TlakRel"])

        while self._history_queue:
            batch = []
            while self._history_queue and len(batch) < batch_size:
                batch.append(self._history_queue.pop(0))

            self._diag_last_batch_size = len(batch)
            self._diag_queue_length = len(self._history_queue)

            # Sčítání chybějících bodů pro diagnostiku
            missing_count = 0
            for m in batch:
                for k in m.keys():
                    if k in allowed_api_keys:
                        missing_count += 1
            self._diag_missing_count = missing_count

            # OPRAVA ŘÁDKU 146: Celou dávku pošleme do jednoho synchronního SQL vlákna naráz
            await self.hass.async_add_executor_job(
                self._insert_history_batch_sync_raw,
                get_instance(self.hass).get_session,
                batch,
                allowed_api_keys
            )

            # Okamžitý přepočet dlouhodobých statistik po úspěšném zápisu dávky
            if self.sensors_payload:
                await self._update_recorder_statistics(self.sensors_payload)

            # Real-time update stavu do entity weather na Lovelace
            weather_entity_id = f"weather.{station_prefix}"
            weather_state = self.hass.states.get(weather_entity_id)
            if weather_state:
                updated_attrs = dict(weather_state.attributes)
                updated_attrs["history_queue_length"] = self._diag_queue_length
                updated_attrs["history_worker_running"] = self._diag_worker_running
                updated_attrs["history_last_batch_size"] = self._diag_last_batch_size
                if self._diag_last_write_ts:
                    updated_attrs["history_last_write_ts"] = self._diag_last_write_ts.isoformat()

                self.hass.states.async_set(weather_entity_id, weather_state.state, updated_attrs)
            
            await asyncio.sleep(pause)

        self._diag_worker_running = False
        self._diag_queue_length = 0
        self._diag_last_batch_size = 0

        # Závěrečný přepočet a vyčištění diagnostiky
        if self.sensors_payload:
            await self._update_recorder_statistics(self.sensors_payload)

        weather_entity_id = f"weather.{station_prefix}"
        weather_state = self.hass.states.get(weather_entity_id)
        if weather_state:
            updated_attrs = dict(weather_state.attributes)
            updated_attrs["history_queue_length"] = 0
            updated_attrs["history_worker_running"] = False
            updated_attrs["history_last_batch_size"] = 0
            self.hass.states.async_set(weather_entity_id, weather_state.state, updated_attrs)

        _LOGGER.debug("Background worker úspěšně dokončil import chybějících mezer")

    def register_delayed_startup(self):
        """Zaregistruje systémový listener, který aktivuje worker až po úplném zavedení HA core."""
        from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

        async def _play_delayed_history_worker(_):
            _LOGGER.debug("Home Assistant plně dokončil start – aktivuji background worker historie")
            self._ha_started = True
            if self._history_queue and (self._history_task is None or self._history_task.done()):
                self._history_task = self.hass.async_create_task(self._history_worker())

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _play_delayed_history_worker)

    # -------------------------------------------------------------------------
    # DLOUHODOBÉ STATISTIKY Z RECORDERU (OPRAVENÁ RYCHLÁ VERZE)
    # -------------------------------------------------------------------------

    async def _update_recorder_statistics(self, data: dict[str, dict]):
        """Načte historii z Recorderu a spočítá statsXXX se stoprocentní přesností."""
        now_utc = dt_util.utcnow()
        start_ts_utc = now_utc - timedelta(hours=self._statistics_interval)
        start_timestamp = start_ts_utc.timestamp()

        station_prefix = self.entry.title.lower().strip().replace(" ", "_")

        for sid, payload in data.items():
            if sid not in SENSOR_DEFINITIONS:
                continue

            internal_sid = API_TO_INTERNAL_MAPPING.get(sid.lower(), sid.lower())
            entity_id = f"sensor.{station_prefix}_{internal_sid}"
            
            # Bezpečné a rychlé volání samostatné metody přes izolovaný HA thread pool
            values = await self.hass.async_add_executor_job(
                _query_recorder_history_sync,
                get_instance(self.hass).get_session, 
                entity_id, 
                start_timestamp
            )

            # JEDNOTNÁ LOGIKA STATISTIK: Pokud máme v DB méně než 20 bodů (čerstvý start),
            # použijeme jako bezpečný fallback stabilní rolling_stats z kompletního JSONu.
            if len(values) < 20: 
                if internal_sid == "vitr_smer":
                    payload["attributes"]["stats_avg"] = payload["attributes"].get("vitr_smer_avg", payload["value"])
                    payload["attributes"]["stats_mode"] = payload["attributes"].get("vitr_smer_mode", payload["value"])
                    payload["attributes"]["stats_var"] = payload["attributes"].get("vitr_smer_var", 0.0)
                else:
                    payload["attributes"]["stats_min"] = payload["attributes"].get("min", payload["value"])
                    payload["attributes"]["stats_max"] = payload["attributes"].get("max", payload["value"])
                continue

            # Kruhová matematika pro směr větru z DB
            if internal_sid == "vitr_smer":
                sin_sum = 0.0
                cos_sum = 0.0
                for val in values:
                    rad = math.radians(val)
                    sin_sum += math.sin(rad)
                    cos_sum += math.cos(rad)

                count = len(values)
                avg_sin = sin_sum / count
                avg_cos = cos_sum / count

                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos)) % 360.0

                rounded = [round(a / 22.5) * 22.5 % 360 for a in values]
                
                # most_common(1) vrací list n-tic, např. [(225.0, 14)].
                # Pomocí [0][0] vytáhneme čistou float hodnotu úhlu (225.0).
                if rounded:
                    common_modes = Counter(rounded).most_common(1)
                    mode_deg = common_modes[0][0] if common_modes else values[0]
                else:
                    mode_deg = values[0] if values else 0.0

                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector))) if 0.001 < r_vector < 1.0 else 0.0

                payload["attributes"]["stats_avg"] = round(avg_deg, 1)
                payload["attributes"]["stats_mode"] = round(mode_deg, 1)  # Nyní už round() získá čisté číslo!
                payload["attributes"]["stats_var"] = round(min(var_deg, 180.0), 1)
            else:
                payload["attributes"]["stats_min"] = min(values)
                payload["attributes"]["stats_max"] = max(values)

    # -------------------------------------------------------------------------
    # TRANSFORMAČNÍ A NORMALIZAČNÍ METODY PRO STRUKTURY HA
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        result: dict[str, dict] = {}
        timestamp_str = datetime.now().isoformat()
        station_prefix = self.entry.title.lower().strip().replace(" ", "_")

        # A. Staticky definované senzory z SENSOR_DEFINITIONS
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

            opts = self._sensor_options.get(sid, DEFAULT_SENSOR_OPTIONS.get(sid, {}))
            
            key_lower = api_key.lower()
            internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)
            target_entity_id = f"sensor.{station_prefix}_{internal_sid}"

            self._entity_id_map[api_key] = target_entity_id

            result[sid] = {
                "value": value,
                "meta": {
                    "name": meta["name"],
                    "unit": meta["unit"],
                    "icon": meta.get("icon"),
                    "device_class": meta.get("device_class"),
                    "state_class": meta.get("state_class"),
                    "type": meta["type"],
                    "order": opts.get("order", meta["order"]),
                    "color": opts.get("color", meta["color"]),
                    "style": opts.get("style", "smooth"),
                    "visible": opts.get("visible", True),
                },
                "attributes": {
                    "timestamp": timestamp_str,
                },
            }

        # B. Dynamicky objevované senzory z doplňkových čidel meteostanice
        for api_key, value in raw.items():
            if api_key in ("Datum", "SrazkyDen", "LokalitaStanice", "DoplCidlaJson", "Historie", "Webkamera", "_computed_ts_utc"):
                continue

            already_mapped = any(m["api_key"] == api_key for m in SENSOR_DEFINITIONS.values())
            if already_mapped:
                continue

            if isinstance(value, str):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass

            meta = get_dynamic_sensor_meta(api_key)
            sid = api_key.lower()
            opts = self._sensor_options.get(sid, {"order": meta["order"], "color": meta["color"], "style": "smooth", "visible": True})

            target_entity_id = f"sensor.{station_prefix}_{sid}"
            self._entity_id_map[api_key] = target_entity_id

            result[sid] = {
                "value": value,
                "meta": {
                    "name": meta["name"],
                    "unit": meta["unit"],
                    "icon": meta.get("icon"),
                    "device_class": meta.get("device_class"),
                    "state_class": meta.get("state_class"),
                    "type": meta["type"],
                    "order": opts["order"],
                    "color": opts["color"],
                    "style": opts["style"],
                    "visible": opts["visible"],
                },
                "attributes": {
                    "timestamp": timestamp_str,
                },
            }

        if "weather" in result:
            result["weather"]["attributes"]["history_queue_length"] = self._diag_queue_length
            result["weather"]["attributes"]["history_worker_running"] = self._diag_worker_running
            result["weather"]["attributes"]["history_missing_count"] = self._diag_missing_count
            result["weather"]["attributes"]["history_last_batch_size"] = self._diag_last_batch_size
            result["weather"]["attributes"]["history_last_write_ts"] = (
                self._diag_last_write_ts.isoformat() if self._diag_last_write_ts else None
            )

        return result

    def _update_rolling_stats(self, data: dict[str, dict], extracted_stats: dict[str, list[float]] | None):
        """Pomocná metoda, která přenese vypočtené unifikované statistiky do atributů entit."""
        if not extracted_stats:
            return

        for sid, payload in data.items():
            values = extracted_stats.get(sid, [])
            if not values and payload.get("value") is not None:
                try:
                    values = [float(payload["value"])]
                except Exception:
                    pass

            if not values:
                continue

            if sid == "vitr_smer":
                sin_sum = sum(math.sin(math.radians(v)) for v in values)
                cos_sum = sum(math.cos(math.radians(v)) for v in values)
                avg_deg = math.degrees(math.atan2(sin_sum / len(values), cos_sum / len(values))) % 360.0
                rounded = [round(a / 22.5) * 22.5 % 360 for a in values]
                mode_deg = Counter(rounded).most_common(1)[0][0] if rounded else values[0]
                r_vector = math.sqrt((sin_sum/len(values))**2 + (cos_sum/len(values))**2)
                var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector))) if 0.001 < r_vector < 1.0 else 0.0
                
                payload["attributes"]["vitr_smer_avg"] = round(avg_deg, 1)
                payload["attributes"]["vitr_smer_mode"] = round(mode_deg, 1)
                payload["attributes"]["vitr_smer_var"] = round(min(var_deg, 180.0), 1)
            else:
                payload["attributes"]["min"] = min(values)
                payload["attributes"]["max"] = max(values)

    # -------------------------------------------------------------------------
    # HLAVNÍ SMYČKA REFRESHOVÁNÍ (MAIN API UPDATE)
    # -------------------------------------------------------------------------

    async def _async_update_data(self):
        """Hlavní asynchronní smyčka koordinátoru. Provádí stažení a unifikovaný průchod."""
        api_key = self.entry.data[CONF_API_KEY]
        url = f"{API_URL_BASE}?KlicApi={api_key}"

        try:
            session = aiohttp_client.async_get_clientsession(self.hass)
            async with asyncio.timeout(20):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise UpdateFailed(f"HTTP {resp.status}")
                    raw = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"API request failed: {err}") from err

        if isinstance(raw, dict) and "Zprava" in raw:
            raise UpdateFailed(f"PočasíMeteo API Error: {raw['Zprava']}")

        history_payload = []
        station_prefix = self.entry.title.lower().strip().replace(" ", "_")

        # Rozklad surového payloadu z API na metadata a historii
        if isinstance(raw, list) and len(raw) > 0:
            meta_payload = raw[0]
            if isinstance(meta_payload, dict):
                self.station_metadata["station_name"] = self.entry.data.get(CONF_STATION)
                self.station_metadata["lokalita_stanice"] = meta_payload.get("LokalitaStanice")
                if "Webkamera" in meta_payload and isinstance(meta_payload["Webkamera"], dict):
                    self.station_metadata["webcamera_url"] = meta_payload["Webkamera"].get("UrlWebcam")

            if len(raw) > 1:
                history_payload = raw[1:]
                raw = history_payload[0]
            else:
                raise UpdateFailed("API response valid, but weather payload missing")

        if "SrazkyDen" in raw:
            self.station_metadata["srazky_den"] = raw["SrazkyDen"]

        # 1. KROK: Unifikovaný průchod nad historií z JSONu (Úlohy 1, 2, 3 a hromadný DB dotaz 4)
        extracted_stats = await self._process_and_import_dataset(history_payload, station_prefix)

        # Fallback zápis intenzity srážek pro aktuální živý stav do Recorderu
        if self._latest_rain_intensity > 0:
            entity_id = f"sensor.{station_prefix}_intenzita_srazek"
            ts = dt_util.utcnow().replace(tzinfo=None)
            await self._insert_history_point(entity_id, self._latest_rain_intensity, ts)
            
        raw["SrazkyIntenzita"] = self._latest_rain_intensity

        # 2. KROK: Normalizace surového živého řádku do struktur HA
        normalized = self._normalize_data(raw)

        # 3. KROK: Přenesení unifikovaných rolling statistik do entit
        self._update_rolling_stats(normalized, extracted_stats)

        # 4. KROK: Finální dlouhodobé statistiky z čisté databáze
        await self._update_recorder_statistics(normalized)

        self.sensors_payload = normalized
        
        # Při úplně prvním startu v rámci __init__.py aktivujeme odložený listener na dokončení bootu HA
        if not self._ha_started and hasattr(self, "register_delayed_startup"):
            self.register_delayed_startup()

        return normalized
