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
    Optimalizovaný a bezpečný zápis historie do Recorderu.
    - metadata se načítají jedním SELECTem
    - shared StateAttributes se načítají jedním SELECTem
    - SELECT je vždy v no_autoflush
    - INSERT probíhá v samostatných transakcích
    - retry/backoff řeší krátkodobé zámky SQLite
    - MultipleResultsFound je ošetřeno – bereme první řádek
    """

    session = session_factory()

    # ---------------------------------------------------------
    # 1) Přednačtení všech entity_id z batche
    # ---------------------------------------------------------
    entity_ids = {m.get("entity_id") for m in batch_points if m.get("entity_id")}
    meta_cache: dict[str, int] = {}

    # ---------------------------------------------------------
    # 2) Přednačtení metadata jedním SELECTem
    # ---------------------------------------------------------
    if entity_ids:
        with session.no_autoflush:
            existing_meta = session.execute(
                select(StatesMeta).where(StatesMeta.entity_id.in_(entity_ids))
            ).all()

        for row in existing_meta:
            meta_obj = row[0]
            # Pokud existuje více řádků, bereme první – Recorder to tak dělá také
            if meta_obj.entity_id not in meta_cache:
                meta_cache[meta_obj.entity_id] = meta_obj.metadata_id

    # ---------------------------------------------------------
    # 3) Přednačtení sdíleného prázdného JSON atributu
    # ---------------------------------------------------------
    with session.no_autoflush:
        attr_rows = session.execute(
            select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
        ).all()

    if attr_rows:
        attr_id = attr_rows[0][0].attributes_id
    else:
        with session.begin():
            attr_row = StateAttributes(shared_attrs="{}")
            session.add(attr_row)
        attr_id = attr_row.attributes_id

    # ---------------------------------------------------------
    # 4) Zápis jednotlivých bodů
    # ---------------------------------------------------------
    for m in batch_points:
        ts = m.get("_computed_ts_utc")
        entity_id = m.get("entity_id")
        value = m.get("value")

        if not ts or not entity_id:
            continue

        utc_timestamp = ts.replace(tzinfo=None).timestamp()

        if value in (None, "", " ", "N/A", "--"):
            continue

        try:
            v_float = float(value)
            if math.isnan(v_float):
                continue
            formatted_state = f"{v_float:.1f}"
        except (ValueError, TypeError):
            formatted_state = str(value)

        # ---------------------------------------------------------
        # 5) Metadata – pokud chybí, vytvoříme je
        # ---------------------------------------------------------
        metadata_id = meta_cache.get(entity_id)
        if metadata_id is None:
            with session.no_autoflush:
                meta_rows = session.execute(
                    select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                ).all()

            if meta_rows:
                # Pokud existuje více řádků, bereme první
                meta_obj = meta_rows[0][0]
            else:
                with session.begin():
                    meta_obj = StatesMeta(entity_id=entity_id)
                    session.add(meta_obj)

            metadata_id = meta_obj.metadata_id
            meta_cache[entity_id] = metadata_id

        # ---------------------------------------------------------
        # 6) INSERT States – samostatná transakce + retry/backoff
        # ---------------------------------------------------------
        retry = 0
        while retry < 5:
            try:
                with session.begin():
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
                break
            except OperationalError:
                retry += 1
                time.sleep(0.15 * retry)
                continue

        if retry == 5:
            _LOGGER.error(
                "PM-TRACE: HISTORY WRITE FAILED AFTER RETRIES for %s", entity_id
            )

    session.close()

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
            update_interval=timedelta(seconds=30),
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

        # Registrace odloženého startu background workeru
        self.register_delayed_startup()

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
        """
        Standardní hook DataUpdateCoordinatoru.
        Stáhne JSON z API, normalizuje ho do payloadu a připraví historii pro Recorder.
        """

        api_key = self.entry.data.get(CONF_API_KEY)
        api_url = f"{API_URL_BASE}?KlicApi={api_key}"

        session = aiohttp_client.async_get_clientsession(self.hass)

        try:
            async with session.get(api_url, timeout=30) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"API returned HTTP {resp.status}")
                data = await resp.json()
        except Exception as err:
            _LOGGER.error("PM-TRACE: API EXCEPTION: %r", err, exc_info=True)
            raise UpdateFailed(f"Cannot fetch PočasíMeteo API: {err}") from err

        # API vrací list: [metadata, current, history...]
        # Normalizace aktuálního měření do payloadu (sid → value/meta/attributes)
        normalized = self._normalize_data(data)
        self.sensors_payload = normalized["sensors"]

        # Historii zpracujeme pomocí již normalizovaných dat
        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")
        history_norm = normalized.get("history", [])
        await self._process_and_import_dataset(history_norm, station_prefix)

        # Po prvním úspěšném update přepneme interval
        if self.update_interval.total_seconds() == 30:
            update_interval_minutes = self.entry.options.get(
                CONF_UPDATE_INTERVAL,
                self.entry.data.get(CONF_UPDATE_INTERVAL, 5),
            )
            self.update_interval = timedelta(minutes=update_interval_minutes)

         # Vždy se pokusíme spočítat statistiky z Recorderu,
        # i když fronta není prázdná (budou založené na tom, co už v DB je)
        if self.sensors_payload:
            await self._update_recorder_statistics(self.sensors_payload)
    
        return self.sensors_payload

    # -------------------------------------------------------------------------
    # UNIFIKOVANÉ ZPRACOVÁNÍ DATASETU (LOGIKA V JEDNOM PRŮCHODU)
    # -------------------------------------------------------------------------

    async def _process_and_import_dataset(self, history_norm, station_prefix):
        if not history_norm:
            return

        try:
            sorted_measurements = sorted(
                history_norm,
                key=lambda m: datetime.fromisoformat(m["datum"]),
            )
        except Exception as err:
            _LOGGER.error("PM-TRACE: HISTORY SORT ERROR: %r", err, exc_info=True)
            return

        queue = []

        for m in sorted_measurements:
            try:
                ts = datetime.fromisoformat(m["datum"])

                points = []
                for sid, value in m.items():
                    if sid == "datum":
                        continue

                    entity_id = f"sensor.{station_prefix}_{sid}"
                    points.append({
                        "entity_id": entity_id,
                        "value": value,
                    })

                queue.append({
                    "ts_utc": ts,
                    "points": points,
                })

            except Exception as err:
                _LOGGER.error("PM-TRACE: HISTORY ITEM ERROR: %r", err, exc_info=True)
                continue

        self._history_queue.extend(queue)
        self._diag_queue_length = len(self._history_queue)

        # Pokud už HA běží a worker není aktivní, spustíme ho
        if self._ha_started and (self._history_task is None or self._history_task.done()):
            self._history_task = self.hass.async_create_task(self._history_worker())

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

            try:
                await recorder.async_add_executor_job(
                    _insert_history_batch_sync_raw,
                    session_factory,
                    batch_points,
                )
                # Uložíme čas zápisu do DB jen při úspěchu
                self._diag_last_write_ts = dt_util.now()
            except Exception as err:
                _LOGGER.error("PM-TRACE: HISTORY WRITE ERROR: %r", err, exc_info=True)
                _LOGGER.error("PM-TRACE: BATCH POINTS SAMPLE: %r", batch_points[:5])
                # Pokud zápis selže, necháme _diag_last_write_ts = None
                # a worker pokračuje na další dávku
                continue

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
        Výsledky ukládá exkluzivně do self.station_metadata["sensor_stats"].
        """

        # Použijeme lokální čas (CEST), protože Recorder má last_changed_ts uložené jako epoch z lokálního času
        now_local = dt_util.now()
        start_local = now_local - timedelta(hours=self._statistics_interval)
        start_timestamp = start_local.timestamp()

        station_prefix = (
            self.entry.data.get(CONF_STATION)
            .lower()
            .strip()
            .replace(" ", "_")
        )

        if "sensor_stats" not in self.station_metadata:
            self.station_metadata["sensor_stats"] = {}

        recorder = get_instance(self.hass)
        session_factory = recorder.get_session

        # HLAVNÍ CYKLUS — iterujeme přes všechny senzory v sensors_payload
        for sid, payload in data.items():

            internal_sid = sid
            entity_id = f"sensor.{station_prefix}_{internal_sid}"

            # Načteme historii z Recorderu
            values = await recorder.async_add_executor_job(
                _query_recorder_history_sync,
                session_factory,
                entity_id,
                start_timestamp,
            )

            # Fallback — Recorder nemá žádná data
            if not values:
                if internal_sid == "vitr_smer":
                    self.station_metadata["sensor_stats"][sid] = {
                        "stats_avg": payload["value"],
                        "stats_mode": payload["value"],
                        "stats_var": 0.0,
                    }
                else:
                    self.station_metadata["sensor_stats"][sid] = {
                        "stats_min": payload["value"],
                        "stats_max": payload["value"],
                    }
                continue

            # --- Vektorové statistiky pro směr větru ---
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

                # Průměrný směr (circular mean)
                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos)) % 360.0

                # Modus (nejčastější směr, zaokrouhlený na 22.5°)
                rounded = [round(a / 22.5) * 22.5 % 360 for a in values]
                if rounded:
                    common_modes = Counter(rounded).most_common(1)
                    mode_deg = common_modes[0][0]
                else:
                    mode_deg = values[0]

                # Variabilita (circular variance)
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

            # --- Standardní statistiky pro ostatní senzory ---
            else:
                self.station_metadata["sensor_stats"][sid] = {
                    "stats_min": round(min(values), 1),
                    "stats_max": round(max(values), 1),
                }

    # -------------------------------------------------------------------------
    # TRANSFORMAČNÍ A NORMALIZAČNÍ METODY PRO STRUKTURY HA
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw_json):
        """
        Přijme celý raw JSON z API PočasíMeteo a:
        - extrahuje metadata, aktuální měření a historii,
        - normalizuje hodnoty do interních klíčů,
        - vytvoří syntetický senzor srazky_intenzita (current + historie),
        - naplní sensors_payload (pro sensor.py),
        - naplní station_metadata (pro weather.py),
        - naplní entity_id mapu,
        - vrátí kompletní normalizovaný dataset.
        """

        # --- 1) Validace formátu API ---
        if not isinstance(raw_json, list) or len(raw_json) < 2:
            raise UpdateFailed("Invalid API response format: expected list with metadata + current + history")

        metadata_raw = raw_json[0] or {}
        current_raw = raw_json[1] or {}
        history_raw = raw_json[2:] if len(raw_json) > 2 else []

        # --- 2) Normalizace aktuálního měření (API → interní klíče) ---
        current = {
            "datum": current_raw.get("Datum"),
            "teplota_vnejsi": self._to_float(current_raw.get("TeplotaVnejsi")),
            "teplota_vnitrni": self._to_float(current_raw.get("TeplotaVnitrni")),
            "tlak_relativni": self._to_float(current_raw.get("TlakRel")),
            "vlhkost_vnejsi": self._to_float(current_raw.get("VlhkostVnejsi")),
            "vlhkost_vnitrni": self._to_float(current_raw.get("VlhkostVnitrni")),
            "slunecni_zareni": self._to_float(current_raw.get("SlunZareni")),
            "uv_index": self._to_float(current_raw.get("UVindex")),
            "vitr_rychlost": self._to_float(current_raw.get("Vitr")),
            "vitr_narazy": self._to_float(current_raw.get("VitrNarazy")),
            "vitr_smer": self._to_int(current_raw.get("VitrSmer")),
            "srazky_den": self._to_float(current_raw.get("SrazkyDen")),
            "lokalita_stanice": metadata_raw.get("LokalitaStanice"),
            "webcamera_url": metadata_raw.get("Webkamera"),
        }

        # --- 3) Výpočet syntetického senzoru srazky_intenzita (current) ---
        srazky_intenzita = 0.0
        if len(history_raw) >= 1 and current_raw.get("SrazkyDen") is not None:
            try:
                rain_now = float(current_raw.get("SrazkyDen", 0))
                prev = history_raw[0]
                rain_prev = float(prev.get("SrazkyDen", 0))

                ts_now = dt_util.parse_datetime(current_raw["Datum"].replace("Z", ""))
                ts_prev = dt_util.parse_datetime(prev["Datum"].replace("Z", ""))

                delta_rain = rain_now - rain_prev
                delta_hours = (ts_now - ts_prev).total_seconds() / 3600.0

                if delta_rain > 0 and delta_hours > 0:
                    srazky_intenzita = round(delta_rain / delta_hours, 2)
            except Exception:
                srazky_intenzita = 0.0

        current["srazky_intenzita"] = srazky_intenzita

        # --- Výpočet stavu počasí (condition) ---
        condition = "cloudy"

        if current["srazky_intenzita"] and current["srazky_intenzita"] > 2:
            condition = "pouring"
        elif current["srazky_intenzita"] and current["srazky_intenzita"] > 0:
            condition = "rainy"
        elif current["slunecni_zareni"] and current["slunecni_zareni"] > 300:
            condition = "sunny"
        elif current["slunecni_zareni"] and current["slunecni_zareni"] > 100:
            condition = "partlycloudy"
        elif current["vitr_rychlost"] and current["vitr_rychlost"] > 10:
            condition = "windy"

        current["condition"] = condition
        self.station_metadata["condition"] = condition

        # --- 4) Normalizace historie (API → interní klíče) + syntetická intenzita ---
        history = []
        prev_h = None

        for item in history_raw:
            h = {
                "datum": item.get("Datum"),
                "teplota_vnejsi": self._to_float(item.get("TeplotaVnejsi")),
                "teplota_vnitrni": self._to_float(item.get("TeplotaVnitrni")),      # NOVÉ
                "tlak_relativni": self._to_float(item.get("TlakRel")),
                "vlhkost_vnejsi": self._to_float(item.get("VlhkostVnejsi")),
                "vlhkost_vnitrni": self._to_float(item.get("VlhkostVnitrni")),      # NOVÉ
                "slunecni_zareni": self._to_float(item.get("SlunZareni")),
                "uv_index": self._to_float(item.get("UVindex")),                    # NOVÉ
                "vitr_rychlost": self._to_float(item.get("Vitr")),
                "vitr_narazy": self._to_float(item.get("VitrNarazy")),
                "vitr_smer": self._to_int(item.get("VitrSmer")),
                "srazky_den": self._to_float(item.get("SrazkyDen")),
                "srazky_intenzita": 0.0,
            }

            # Výpočet intenzity pro historický bod
            if prev_h is not None:
                try:
                    rain_now = h["srazky_den"]
                    rain_prev = prev_h["srazky_den"]

                    ts_now = dt_util.parse_datetime(h["datum"].replace("Z", ""))
                    ts_prev = dt_util.parse_datetime(prev_h["datum"].replace("Z", ""))

                    delta_rain = rain_now - rain_prev
                    delta_hours = (ts_now - ts_prev).total_seconds() / 3600.0

                    if delta_rain > 0 and delta_hours > 0:
                        h["srazky_intenzita"] = round(delta_rain / delta_hours, 2)
                except Exception:
                    h["srazky_intenzita"] = 0.0

            history.append(h)
            prev_h = h

        # --- 5) Naplnění sensors_payload (pro sensor.py) ---
        sensors_payload = {}
        station_prefix = self.entry.data.get(CONF_STATION).lower().strip().replace(" ", "_")

        for sid, meta in SENSOR_DEFINITIONS.items():
            api_key = meta["api_key"].lower()
            internal_sid = API_TO_INTERNAL_MAPPING.get(api_key, api_key)

            value = current.get(internal_sid)

            sensors_payload[internal_sid] = {
                "value": value,
                "attributes": {
                    "timestamp": current.get("datum"),
                    "vitr_smer_avg": None,
                    "vitr_smer_mode": None,
                    "vitr_smer_var": None,
                },
            }

            self._entity_id_map[api_key] = f"sensor.{station_prefix}_{internal_sid}"

        # --- 6) Přidání syntetického senzoru do sensors_payload ---
        sensors_payload["srazky_intenzita"] = {
            "value": current["srazky_intenzita"],
            "attributes": {
                "timestamp": current.get("datum"),
            },
        }
        self._entity_id_map["srazky_intenzita"] = f"sensor.{station_prefix}_srazky_intenzita"

        # --- 7) Naplnění station_metadata (pro weather.py) ---
        self.station_metadata["lokalita_stanice"] = current.get("lokalita_stanice")
        self.station_metadata["srazky_den"] = current.get("srazky_den", 0)
        self.station_metadata["webcamera_url"] = current.get("webcamera_url")

        api_ts_raw = current.get("datum")
        if api_ts_raw:
            try:
                self.station_metadata["api_timestamp"] = dt_util.parse_datetime(
                    api_ts_raw.replace("Z", "")
                ).isoformat()
            except Exception:
                self.station_metadata["api_timestamp"] = dt_util.now().isoformat()
        else:
            self.station_metadata["api_timestamp"] = None

        # --- 8) Vrácení kompletního normalizovaného datasetu ---
        return {
            "metadata": metadata_raw,
            "current": current,
            "history": history,
            "sensors": sensors_payload,
        }
        
    def _to_float(self, value):
        """Bezpečný převod na float."""
        try:
            if value in (None, "", " ", "N/A", "--"):
                return None
            return float(value)
        except Exception:
            return None

    def _to_int(self, value):
        """Bezpečný převod na int."""
        try:
            if value in (None, "", " ", "N/A", "--"):
                return None
            return int(float(value))
        except Exception:
            return None
