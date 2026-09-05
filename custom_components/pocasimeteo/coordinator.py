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
# ČISTÉ SYNCHRONNÍ DATABÁZOVÉ FUNKCE (DEFINOVANÉ MIMO TŘÍDY COORDINATORU)
# =========================================================================


def _query_recorder_history_sync(session_factory, target_entity_id, start_timestamp):
    """Čistě synchronní I/O dotaz do Recorderu, spuštěný odděleně v thread poolu."""
    with session_factory() as session:
        rows = session.execute(
            select(States.state)
            .where(
                States.entity_id == target_entity_id,
                States.last_changed_ts >= start_timestamp,
            )
        ).all()

    values = []
    for (state_val,) in rows:
        if state_val in (None, "", "unknown", "unavailable"):
            continue
        try:
            v = float(state_val)
            if not math.isnan(v):
                values.append(v)
        except Exception:
            continue
    return values


def _query_existing_timestamps_sync(session_factory, sample_entity, processed_timestamps):
    """Hromadně ověří existenci celé sady timestampů v DB v synchronním executoru."""
    with session_factory() as session:
        rows = session.execute(
            select(States.last_changed_ts)
            .where(
                States.entity_id == sample_entity,
                States.last_changed_ts.in_(processed_timestamps),
            )
        ).all()
    return {float(r[0]) for r in rows if r and r[0] is not None}


def _insert_history_batch_sync_raw(session_factory, batch_points: list[dict]):
    """
    Kompletní hromadný zápis celé dávky v jednom synchronním DB vlákně.
    Tato verze již NEPRACUJE s API klíči – používá přímo entity_id, hodnotu a timestamp.
    """
    with session_factory() as session:
        meta_cache: dict[str, int] = {}
        attr_id = None

        for m in batch_points:
            ts = m.get("_computed_ts_utc")
            entity_id = m.get("entity_id")
            value = m.get("value")

            if not ts or not entity_id:
                continue

            # Převod času na float timestamp
            utc_timestamp = ts.replace(tzinfo=None).timestamp()

            # Konverze hodnoty na float nebo string
            if value in (None, "", " ", "N/A", "--"):
                continue

            try:
                v_float = float(value)
                if math.isnan(v_float):
                    continue
                formatted_state = f"{v_float:.1f}"
            except (ValueError, TypeError):
                formatted_state = str(value)

            # Metadata (StatesMeta)
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

            # Attributes (StateAttributes) – sdílený prázdný JSON
            if attr_id is None:
                attr_row = session.execute(
                    select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
                ).scalar_one_or_none()

                if not attr_row:
                    attr_row = StateAttributes(shared_attrs="{}")
                    session.add(attr_row)
                    session.flush()

                attr_id = attr_row.attributes_id

            # Vytvoření řádku States
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

        # Commit celé dávky
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
        _LOGGER.error("PM-TRACE: _async_update_data() START")
        """
        Standardní hook DataUpdateCoordinatoru.
        Stáhne JSON z API, normalizuje ho do payloadu a připraví historii pro Recorder.
        """
        session = aiohttp_client.async_get_clientsession(self.hass)

        api_key = self.entry.data.get(CONF_API_KEY)
        station_name = self.entry.data.get(CONF_STATION)

        params = {
            "apiKey": api_key,
            "station": station_name,
        }

        try:
            async with session.get(API_URL_BASE, params=params, timeout=30) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"API returned HTTP {resp.status}")
                data = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Cannot fetch PočasíMeteo API: {err}") from err

        # JSON očekává strukturu: hlavní aktuální měření + pole "Historie"
        current = data.get("Aktualni", data)
        history = data.get("Historie", [])

        # Normalizace aktuálního měření do payloadu (sid → value/meta/attributes)
        _LOGGER.error("PM-TRACE: calling _normalize_data()")
        normalized = self._normalize_data(current)
        _LOGGER.error("PM-TRACE: _normalize_data() DONE")
        self.sensors_payload = normalized
        _LOGGER.error(f"PM-TRACE: sensors_payload keys = {list(self.sensors_payload.keys())}")

        # Uložení základních metadat stanice
        self.station_metadata["lokalita_stanice"] = current.get("LokalitaStanice")
        self.station_metadata["srazky_den"] = current.get("SrazkyDen", 0)
        self.station_metadata["webcamera_url"] = current.get("Webkamera")
        
        # Timestamp z API – pro frontend kartu
        api_ts_raw = current.get("Datum")
        if api_ts_raw:
            try:
                self.station_metadata["api_timestamp"] = dt_util.parse_datetime(
                    api_ts_raw.replace("Z", "")
                ).isoformat()
            except Exception:
                self.station_metadata["api_timestamp"] = dt_util.now().isoformat()

        # Zpracování datasetu historie – výpočet intenzity srážek, rolling statistik
        # a příprava payload‑centrické fronty pro Recorder
        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        _LOGGER.error("PM-TRACE: calling _process_and_import_dataset()")
        await self._process_and_import_dataset(history, station_prefix)
        _LOGGER.error("PM-TRACE: _process_and_import_dataset() DONE")

        return self.sensors_payload

    # -------------------------------------------------------------------------
    # UNIFIKOVANÉ ZPRACOVÁNÍ DATASETU (LOGIKA V JEDNOM PRŮCHODU)
    # -------------------------------------------------------------------------

    async def _process_and_import_dataset(self, measurements: list[dict], station_prefix: str):
        """
        Sloučená logika:
        1) spočítá intenzitu srážek,
        2) naplní rolling statistiky,
        3) připraví payload‑centrickou frontu pro Recorder (entity_id + value + ts),
        4) spustí background worker, pokud jsou v DB mezery.
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
        prepared_history_points: list[dict] = []
        processed_timestamps = set()

        live_boundary = time.time() - 600  # 10 minut
        local_tz = dt_util.get_time_zone(self.hass.config.time_zone)

        # A. HLAVNÍ JEDINÝ CYKLUS NAD DATASETEM
        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            try:
                ts_utc_with_tz = dt_util.parse_datetime(ts_raw.replace("Z", ""))
                utc_timestamp = ts_utc_with_tz.timestamp()
                ts_utc_naive = ts_utc_with_tz.replace(tzinfo=None)
            except Exception as e:
                _LOGGER.error("Chyba při konverzi času u bodu %s: %s", ts_raw, e)
                continue

            # Výpočet intenzity srážek (syntetický senzor intenzita_srazek)
            try:
                rain_total = float(m.get("SrazkyDen", 0))
            except Exception:
                rain_total = 0.0

            if previous_rain is not None:
                delta_rain = rain_total - previous_rain
                delta_time = (ts_utc_naive - previous_ts).total_seconds() / 3600.0
                intensity = round(delta_rain / delta_time, 2) if (delta_rain > 0 and delta_time > 0) else 0.0
            else:
                intensity = 0.0

            previous_rain = rain_total
            previous_ts = ts_utc_naive

            # Uložíme poslední intenzitu pro aktuální běh
            self._latest_rain_intensity = intensity

            # Sběr dat pro rolling statistiky – pracujeme už jen s interními sid
            for sid, meta in SENSOR_DEFINITIONS.items():
                api_key = meta["api_key"]
                key_lower = api_key.lower()
                internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)

                # Speciální případ: intenzita srážek je syntetická veličina
                if internal_sid == "intenzita_srazek":
                    value = intensity
                else:
                    value = m.get(api_key)

                if value in (None, "", " ", "N/A", "--"):
                    continue

                try:
                    v_float = float(value)
                    if math.isnan(v_float):
                        continue
                except Exception:
                    continue

                extracted_stats.setdefault(internal_sid, []).append(v_float)

            # Příprava bodu pro historii (pokud je starší než 10 minut)
            if utc_timestamp <= live_boundary:
                # Pro každý statický senzor připravíme payload‑centrický bod:
                points_for_ts: list[dict] = []
                for sid, meta in SENSOR_DEFINITIONS.items():
                    api_key = meta["api_key"]
                    key_lower = api_key.lower()
                    internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)

                    if internal_sid == "intenzita_srazek":
                        value = intensity
                    else:
                        value = m.get(api_key)

                    if value in (None, "", " ", "N/A", "--"):
                        continue

                    # Najdeme entity_id z registru (byl naplněn v __init__ a _normalize_data)
                    entity_id = self._entity_id_map.get(api_key.lower())
                    if not entity_id:
                        # Fallback – deterministické odvození
                        entity_id = f"sensor.{station_prefix}_{internal_sid}"

                    try:
                        v_float = float(value)
                        if math.isnan(v_float):
                            continue
                    except Exception:
                        continue

                    points_for_ts.append(
                        {
                            "_computed_ts_utc": ts_utc_naive,
                            "entity_id": entity_id,
                            "value": v_float,
                        }
                    )

                if points_for_ts:
                    prepared_history_points.append(
                        {
                            "ts_utc": ts_utc_naive,
                            "ts_float": utc_timestamp,
                            "points": points_for_ts,
                        }
                    )
                    processed_timestamps.add(utc_timestamp)

        # B. JEDEN HROMADNÝ DOTAZ DO DB (ODSTRANĚNÍ DUPLICIT S KONTROLOU DOSTUPNOSTI RECORDERU)
        final_queue: list[dict] = []

        if prepared_history_points:
            recorder_instance = None
            try:
                recorder_instance = get_instance(self.hass)
            except Exception:
                recorder_instance = None

            if recorder_instance and hasattr(recorder_instance, "get_session"):
                sample_entity = f"sensor.{station_prefix}_teplota_vnejsi"
                try:
                    session_factory = recorder_instance.get_session
                    existing_timestamps = await recorder_instance.async_add_executor_job(
                        _query_existing_timestamps_sync,
                        session_factory,
                        sample_entity,
                        processed_timestamps,
                    )
                except Exception as db_err:
                    _LOGGER.warning(
                        "Hromadný dotaz na existenci historie selhal (DB se zavedla, ale neodpovídá): %s",
                        db_err,
                    )
                    existing_timestamps = set()

                # Do fronty pustíme POUZE ty body, které prokazatelně v databázi ještě NEJSOU
                final_queue = [
                    pt for pt in prepared_history_points if pt["ts_float"] not in existing_timestamps
                ]
            else:
                _LOGGER.debug(
                    "Recorder při startu integrace ještě není inicializován. "
                    "Odkládám filtraci historie na později."
                )
                final_queue = []

        # C. SPUŠTĚNÍ WORKERU (OCHRANA PŘED NEKONEČNOU SMYČKOU A ZACYKLENÍM)
        if final_queue:
            if self._diag_worker_running:
                _LOGGER.debug("Worker historie již běží. Vynechávám duplicitní plnění fronty.")
            else:
                # Fronta nyní obsahuje payload‑centrické body (ts + points[entity_id,value])
                self._history_queue = final_queue
                self._diag_queue_length = len(self._history_queue)

                if self._ha_started and (self._history_task is None or self._history_task.done()):
                    _LOGGER.debug(
                        "Spouštím background worker pro doplnění mezer (velikost: %s)",
                        self._diag_queue_length,
                    )
                    self._history_task = self.hass.async_create_task(self._history_worker())
        else:
            _LOGGER.debug(
                "Všechna historická data z JSONu již v DB existují. Vynechávám spuštění workeru."
            )

        # D. Uložení rolling statistik do station_metadata["sensor_stats"] (RAM)
        if "sensor_stats" not in self.station_metadata:
            self.station_metadata["sensor_stats"] = {}

        for sid, meta in SENSOR_DEFINITIONS.items():
            key_lower = meta["api_key"].lower()
            internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)
            values = extracted_stats.get(internal_sid, [])

            if not values:
                continue

            if internal_sid == "vitr_smer":
                # Kruhová matematika pro směr větru
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

                if rounded:
                    common_modes = Counter(rounded).most_common(1)
                    mode_deg = common_modes[0][0] if common_modes else values[0]
                else:
                    mode_deg = values[0] if values else 0.0

                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                var_deg = (
                    math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                    if 0.001 < r_vector < 1.0
                    else 0.0
                )

                self.station_metadata["sensor_stats"][sid] = {
                    "stats_avg": round(avg_deg, 1),
                    "stats_mode": round(mode_deg, 1),
                    "stats_var": round(min(var_deg, 180.0), 1),
                }
            else:
                self.station_metadata["sensor_stats"][sid] = {
                    "stats_min": round(min(values), 1),
                    "stats_max": round(max(values), 1),
                }

        return extracted_stats

    # -------------------------------------------------------------------------
    # HISTORICKÝ BACKGROUND WORKER & ODLOŽENÝ START
    # -------------------------------------------------------------------------

    async def _history_worker(self):
        """Background worker, který bezpečně a hromadně deleguje zápis dávek do executoru."""
        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        batch_size = 60
        pause = 0.2

        self._diag_worker_running = True

        while self._history_queue:
            batch_ts_items: list[dict] = []
            while self._history_queue and len(batch_ts_items) < batch_size:
                batch_ts_items.append(self._history_queue.pop(0))

            # Flatten: z každého timestampu vytáhneme jednotlivé body (entity_id + value + ts)
            batch_points: list[dict] = []
            for item in batch_ts_items:
                ts_utc = item["ts_utc"]
                for p in item["points"]:
                    batch_points.append(
                        {
                            "_computed_ts_utc": ts_utc,
                            "entity_id": p["entity_id"],
                            "value": p["value"],
                        }
                    )

            self._diag_last_batch_size = len(batch_points)
            self._diag_queue_length = len(self._history_queue)

            # Sčítání chybějících bodů pro diagnostiku
            self._diag_missing_count = len(batch_points)

            # Celou dávku pošleme do jednoho synchronního SQL vlákna naráz
            recorder = get_instance(self.hass)
            session_factory = recorder.get_session

            await recorder.async_add_executor_job(
                _insert_history_batch_sync_raw,
                session_factory,
                batch_points,
            )

            # Uložíme čas zápisu do DB
            self._diag_last_write_ts = dt_util.now()

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

        # --- KONEC CYKLU WORKERU (FRONTA JE 0) ---
        self._diag_worker_running = False
        self._diag_queue_length = 0
        self._diag_last_batch_size = 0

        # Po úspěšném importu celé historie vyvoláme přepočet dlouhodobých statistik z DB
        if self.sensors_payload:
            await self._update_recorder_statistics(self.sensors_payload)

        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        weather_entity_id = f"weather.{station_prefix}"
        weather_state = self.hass.states.get(weather_entity_id)
        if weather_state:
            updated_attrs = dict(weather_state.attributes)
            updated_attrs["history_queue_length"] = 0
            updated_attrs["history_worker_running"] = False
            updated_attrs["history_last_batch_size"] = 0

            if "sensor_stats" in self.station_metadata:
                updated_attrs["sensor_stats"] = self.station_metadata["sensor_stats"]

            if self._diag_last_write_ts:
                updated_attrs["history_last_write_ts"] = self._diag_last_write_ts.isoformat()

            self.hass.states.async_set(weather_entity_id, weather_state.state, updated_attrs)

        _LOGGER.debug("Background worker úspěšně dokončil import chybějících mezer a uvolnil zámek")

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
        """
        Načte historii ze SQL Recorderu a spočítá dlouhodobé statistiky.
        Výsledky ukládá exkluzivně do extended slovníku ve self.station_metadata["sensor_stats"].
        """
        now_utc = dt_util.utcnow()
        start_ts_utc = now_utc - timedelta(hours=self._statistics_interval)
        start_timestamp = start_ts_utc.timestamp()

        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")

        if "sensor_stats" not in self.station_metadata:
            self.station_metadata["sensor_stats"] = {}

        for sid, payload in data.items():
            internal_sid = sid
            entity_id = f"sensor.{station_prefix}_{internal_sid}"

            recorder = get_instance(self.hass)
            session_factory = recorder.get_session

            values = await recorder.async_add_executor_job(
                _query_recorder_history_sync,
                session_factory,
                entity_id,
                start_timestamp,
            )

            if len(values) < 10:
                if internal_sid == "vitr_smer":
                    self.station_metadata["sensor_stats"][sid] = {
                        "stats_avg": payload["attributes"].get("vitr_smer_avg", payload["value"]),
                        "stats_mode": payload["attributes"].get("vitr_smer_mode", payload["value"]),
                        "stats_var": payload["attributes"].get("vitr_smer_var", 0.0),
                    }
                else:
                    self.station_metadata["sensor_stats"][sid] = {
                        "stats_min": payload["value"],
                        "stats_max": payload["value"],
                    }
                continue

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

                if rounded:
                    common_modes = Counter(rounded).most_common(1)
                    mode_deg = common_modes[0][0] if common_modes else values[0]
                else:
                    mode_deg = values[0] if values else 0.0

                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                var_deg = (
                    math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                    if 0.001 < r_vector < 1.0
                    else 0.0
                )

                self.station_metadata["sensor_stats"][sid] = {
                    "stats_avg": round(avg_deg, 1),
                    "stats_mode": round(mode_deg, 1),
                    "stats_var": round(min(var_deg, 180.0), 1),
                }
            else:
                self.station_metadata["sensor_stats"][sid] = {
                    "stats_min": round(min(values), 1),
                    "stats_max": round(max(values), 1),
                }

    # -------------------------------------------------------------------------
    # TRANSFORMAČNÍ A NORMALIZAČNÍ METODY PRO STRUKTURY HA
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        """
        Transformuje syrový JSON aktuálního měření na payload[sid],
        včetně metadat, timestampu a mapování na entity_id.
        """
        result: dict[str, dict] = {}
        timestamp_str = dt_util.now().isoformat()
        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")

        # A. Staticky definované senzory z SENSOR_DEFINITIONS
        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"]
            value = raw.get(api_key)

            # Speciální případ: intenzita srážek – použijeme poslední spočtenou hodnotu
            key_lower = api_key.lower()
            internal_sid = API_TO_INTERNAL_MAPPING.get(key_lower, key_lower)
            if internal_sid == "intenzita_srazek":
                # Pokud API neposílá přímo intenzitu, použijeme hodnotu z posledního výpočtu
                if value is None:
                    value = self._latest_rain_intensity

            if value is None:
                continue

            if isinstance(value, str):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass

            opts = self._sensor_options.get(sid, DEFAULT_SENSOR_OPTIONS.get(sid, {}))
            target_entity_id = f"sensor.{station_prefix}_{internal_sid}"

            # Registr mapování API klíče na entity_id – pro případné fallbacky
            self._entity_id_map[api_key.lower()] = target_entity_id

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

            # Rolling min/max pro lineární senzory – zůstanou v RAM
            internal_sid_for_stats = internal_sid
            if internal_sid_for_stats != "vitr_smer":
                # Základní rolling statistiky pro senzory (min/max) – z aktuálního běhu
                # (dlouhodobé statistiky z Recorderu se počítají zvlášť)
                result[sid]["attributes"]["min"] = value
                result[sid]["attributes"]["max"] = value

        # B. Dynamicky objevované senzory z doplňkových čidel
        for api_key, value in raw.items():
            if api_key in (
                "Datum",
                "SrazkyDen",
                "LokalitaStanice",
                "DoplCidlaJson",
                "Historie",
                "Webkamera",
                "_computed_ts_utc",
            ):
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
            opts = self._sensor_options.get(
                sid,
                {"order": meta["order"], "color": meta["color"], "style": "smooth", "visible": True},
            )

            target_entity_id = f"sensor.{station_prefix}_{sid}"
            self._entity_id_map[sid] = target_entity_id

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

        # CENTRÁLNÍ PUBLIKACE DIAGNOSTIKY DO GLOBÁLNÍCH METADAT WEATHER
        self.station_metadata["history_queue_length"] = self._diag_queue_length
        self.station_metadata["history_worker_running"] = self._diag_worker_running
        self.station_metadata["history_missing_count"] = self._diag_missing_count
        self.station_metadata["history_last_batch_size"] = self._diag_last_batch_size

        return result
