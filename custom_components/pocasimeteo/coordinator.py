"""Data update coordinator for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
import math
from collections import Counter
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import aiohttp_client

# Recorder components pro moderní DB schéma Home Assistenta
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta, StateAttributes
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
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Koordinátor odpovědný za stahování dat, plnění mezer v historii databáze
    a výpočet statistik pro potřeby frontendové karty.
    """

    # -------------------------------------------------------------------------
    # Recorder helpers
    # -------------------------------------------------------------------------

    async def _history_exists(self, entity_id: str, ts: datetime) -> bool:
        rec = get_instance(self.hass)

        def _check():
            with rec.get_session() as session:
                q = select(States).where(
                    States.entity_id == entity_id,
                    States.last_changed == ts,
                )
                return session.execute(q).first() is not None

        return await self.hass.async_add_executor_job(_check)

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        rec = get_instance(self.hass)

        def _insert():
            with rec.get_session() as session:
                # Metadata
                meta_row = session.execute(
                    select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                ).scalar_one_or_none()

                if not meta_row:
                    meta_row = StatesMeta(entity_id=entity_id)
                    session.add(meta_row)
                    session.flush()

                metadata_id = meta_row.metadata_id

                # Attributes
                attr_row = session.execute(
                    select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
                ).scalar_one_or_none()

                if not attr_row:
                    attr_row = StateAttributes(shared_attrs="{}")
                    session.add(attr_row)
                    session.flush()

                attributes_id = attr_row.attributes_id

                # State
                row = States(
                    entity_id=entity_id,
                    metadata_id=metadata_id,
                    attributes_id=attributes_id,
                    state=str(value),
                    last_changed=ts,
                    last_updated=ts,
                )
                session.add(row)
                session.commit()

        await self.hass.async_add_executor_job(_insert)

    # -------------------------------------------------------------------------
    # Import historie – příprava fronty + background worker
    # -------------------------------------------------------------------------

    async def _import_history(self, measurements: list[dict]):
        """Připraví historii z JSONu API do fronty pro pomalý import do databáze."""

        # Seřadíme historii podle času
        sorted_measurements = sorted(
            measurements,
            key=lambda m: datetime.fromisoformat(m["Datum"]),
        )

        previous_rain = None
        previous_ts = None

        # Výpočet intenzity srážek
        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            ts = datetime.fromisoformat(ts_raw).replace(tzinfo=None)

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

        if sorted_measurements:
            self._latest_rain_intensity = sorted_measurements[-1].get(
                "SrazkyIntenzita", 0.0
            )

        # frontu přepisujeme
        self._history_queue = list(sorted_measurements)

        # DIAGNOSTIKA: aktuální délka fronty
        self._diag_queue_length = len(self._history_queue)

        # Worker se spouští jen když HA už běží
        if self._ha_started:
            if self._history_task is None or self._history_task.done():
                _LOGGER.debug("Spouštím background worker pro import historie")
                self._history_task = self.hass.async_create_task(self._history_worker())
        else:
            _LOGGER.debug("HA se teprve startuje – worker nebude spuštěn")

    async def _history_worker(self):
        """Background worker, který po dávkách doplňuje historii do Recorderu."""

        station_prefix = self.entry.title.lower().replace(" ", "_")

        batch_size = 60  # menší dávky
        pause = 0.1      # delší pauza mezi dávkami

        # DIAGNOSTIKA: worker běží
        self._diag_worker_running = True

        while self._history_queue:
            batch: list[dict] = []
            while self._history_queue and len(batch) < batch_size:
                batch.append(self._history_queue.pop(0))

            # DIAGNOSTIKA: velikost poslední dávky + aktuální délka fronty
            self._diag_last_batch_size = len(batch)
            self._diag_queue_length = len(self._history_queue)

            await self._import_history_batch(station_prefix, batch)

            await asyncio.sleep(pause)

        # DIAGNOSTIKA: worker skončil, fronta prázdná
        self._diag_worker_running = False
        self._diag_queue_length = len(self._history_queue)

        _LOGGER.debug("Background worker pro import historie dokončil práci")

    async def _import_history_batch(self, station_prefix: str, measurements: list[dict]):
        """Zapíše jednu dávku historických bodů do Recorderu."""

        api_to_internal_mapping = {
            "teplotavnejsi": "teplota_vnejsi",
            "vlhkostvnejsi": "vlhkost_vnejsi",
            "tlakrel": "tlak_relativni",
            "srazkyintenzita": "intenzita_srazek",
            "vitr": "vitr_rychlost",
            "vitrnarazy": "vitr_narazy",
            "vitrsmer": "vitr_smer",
            "slunzareni": "slunecni_zareni",
            "uvindex": "uv_index",
            "teplotavnitrni": "teplota_vnitrni",
            "vlhkostvnitrni": "vlhkost_vnitrni",
        }

        missing = 0

        for m in measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            # Předpokládáme, že API posílá lokální čas stanice. Převedeme ho na UTC, se kterým pracuje DB.
            from homeassistant.util import dt as dt_util
            local_ts = datetime.fromisoformat(ts_raw)
            # Pokud v const.py nebo jinde máte definované časové pásmo stanice, použijte dt_util.as_utc()
            ts = dt_util.as_utc(local_ts).replace(tzinfo=None)

            # DIAGNOSTIKA: timestamp posledního zápisu (poslední zpracovaný bod v dávce)
            self._diag_last_write_ts = ts

            for key, value in m.items():
                if key in ("Datum", "LokalitaStanice", "DoplCidlaJson"):
                    continue

                # validace hodnot
                if value in (None, "", " ", "N/A", "--"):
                    continue

                try:
                    v = float(value)
                except Exception:
                    continue

                if math.isnan(v):
                    continue

                key_lower = key.lower()
                internal_sid = api_to_internal_mapping.get(key_lower, key_lower)
                entity_id = f"sensor.{station_prefix}_{internal_sid}"

                if not await self._history_exists(entity_id, ts):
                    missing += 1
                    await self._insert_history_point(entity_id, v, ts)

        # DIAGNOSTIKA: kolik bodů v této dávce bylo skutečně doplněno
        self._diag_missing_count = missing

    # -------------------------------------------------------------------------
    # Coordinator initialization
    # -------------------------------------------------------------------------

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

    # -------------------------------------------------------------------------
    # Main API update
    # -------------------------------------------------------------------------

    async def _async_update_data(self):
        """Hlavní smyčka stažení dat z API."""
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

        history_payload = None

        # Zpracování metadat stanice a extrakce payloadu počasí
        if isinstance(raw, list) and len(raw) > 0:
            meta_payload = raw[0]
            if isinstance(meta_payload, dict):
                self.station_metadata["station_name"] = self.entry.data.get(
                    CONF_STATION
                )
                self.station_metadata["lokalita_stanice"] = meta_payload.get(
                    "LokalitaStanice"
                )
                if "Webkamera" in meta_payload and isinstance(
                    meta_payload["Webkamera"], dict
                ):
                    self.station_metadata["webcamera_url"] = meta_payload[
                        "Webkamera"
                    ].get("UrlWebcam")

            if isinstance(raw, list) and len(raw) > 1:
                history_payload = raw[1:]
                raw = history_payload[0]
            else:
                raise UpdateFailed(
                    "API response structure valid, but weather payload missing"
                )

        if "SrazkyDen" in raw:
            self.station_metadata["srazky_den"] = raw["SrazkyDen"]

        # Import historie – ale ne při prvním refreshi
        if isinstance(history_payload, list) and len(history_payload) > 0:
            try:
                if self._ha_started:
                    await self._import_history(history_payload)
                else:
                    _LOGGER.debug("První refresh – historie se neimportuje")
            except Exception as hist_err:
                _LOGGER.warning("Import historie PočasíMeteo selhal: %s", hist_err)

        # Fallback intenzita srážek
        if "SrazkyDen" in raw:
            try:
                rain_series = self._rolling_history.setdefault("srazky_den_raw", [])
                curr_val = float(raw["SrazkyDen"])
                rain_series.append((datetime.now(), curr_val))

                if len(rain_series) >= 2:
                    prev_val = rain_series[-2][1]
                    delta = curr_val - prev_val
                    if delta > 0:
                        self._latest_rain_intensity = round(delta / 0.0833, 2)
            except Exception as e:
                _LOGGER.debug("Fallback intensity calculation failed: %s", e)

        if self._latest_rain_intensity > 0:
            entity_id = (
                f"sensor.{self.entry.title.lower().replace(' ', '_')}_intenzita_srazek"
            )
            ts = datetime.now().replace(tzinfo=None)
            await self._insert_history_point(entity_id, self._latest_rain_intensity, ts)

        raw["SrazkyIntenzita"] = self._latest_rain_intensity

        normalized = self._normalize_data(raw)
        self._update_rolling_stats(normalized)

        # Statistické atributy z Recorderu
        await self._update_recorder_statistics(normalized)

        self.sensors_payload = normalized
        self._ha_started = True

        return normalized

    
    # -------------------------------------------------------------------------
    # Výpočet statistik z Recorderu – OPRAVENÁ VERZE
    # -------------------------------------------------------------------------

    async def _update_recorder_statistics(self, data: dict[str, dict]):
        """Načte historii z Recorderu a spočítá statistiky podle konfigurovaného intervalu."""
        from homeassistant.util import dt as dt_util

        rec = get_instance(self.hass)
        
        # Home Assistant ukládá stavy do DB výhradně v UTC.
        # Musíme vzít aktuální čas v UTC, aby dotaz na historii lícoval s databází.
        now_utc = dt_util.utcnow()
        start_ts_utc = now_utc - timedelta(hours=self._statistics_interval)

        station_prefix = self.entry.title.lower().replace(" ", "_")

        # Mapovací slovník identický s _import_history_batch, abychom sahali pro správná entity_id
        api_to_internal_mapping = {
            "teplatavnejsi": "teplota_vnejsi",
            "vlhkostvnejsi": "vlhkost_vnejsi",
            "tlakrel": "tlak_relativni",
            "srazkyintenzita": "intenzita_srazek",
            "vitr": "vitr_rychlost",
            "vitrnarazy": "vitr_narazy",
            "vitrsmer": "vitr_smer",
            "slunzareni": "slunecni_zareni",
            "uvindex": "uv_index",
            "teplotavnitrni": "teplota_vnitrni",
            "vlhkostvnitrni": "vlhkost_vnitrni",
        }

        def _load_history(target_entity_id: str):
            with rec.get_session() as session:
                rows = session.execute(
                    select(States.state, States.last_changed)
                    .where(
                        States.entity_id == target_entity_id,
                        States.last_changed >= start_ts_utc,
                    )
                    .order_by(States.last_changed.asc())
                ).all()
            return rows

        for sid, payload in data.items():
            # weather entity není senzor, statistiky počítáme jen pro senzory
            if sid not in SENSOR_DEFINITIONS:
                continue

            # Převod sid na správné vnitřní ID entity (zohlednění podtržítkových forem)
            internal_sid = api_to_internal_mapping.get(sid.lower(), sid.lower())
            entity_id = f"sensor.{station_prefix}_{internal_sid}"
            
            rows = await self.hass.async_add_executor_job(_load_history, entity_id)

            values: list[float] = []
            for state, ts in rows:
                if state in (None, "", "unknown", "unavailable"):
                    continue
                try:
                    v = float(state)
                    if not math.isnan(v):
                        values.append(v)
                except Exception:
                    continue

            if not values:
                # Pokud v DB ještě nejsou data, použijeme jako fallback aktuální hodnotu z API
                try:
                    current_val = float(payload["value"])
                    if not math.isnan(current_val):
                        values = [current_val]
                except Exception:
                    continue

            if not values:
                continue

            # Směr větru – kruhové statistiky avg/mode/var
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

                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos))
                if avg_deg < 0:
                    avg_deg += 360.0

                rounded = [round(a / 22.5) * 22.5 % 360 for a in values]
                mode_deg = Counter(rounded).most_common(1)[0][0]

                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                if 0.001 < r_vector < 1.0:
                    var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                else:
                    var_deg = 0.0

                payload["attributes"]["stats_avg"] = round(avg_deg, 1)
                payload["attributes"]["stats_mode"] = round(mode_deg, 1)
                payload["attributes"]["stats_var"] = round(min(var_deg, 180.0), 1)

            # Ostatní senzory – přesný výpočet min/max za nakonfigurovaný interval
            else:
                payload["attributes"]["stats_min"] = min(values)
                payload["attributes"]["stats_max"] = max(values)

    # -------------------------------------------------------------------------
    # Normalize API payload into HA sensor format
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        result: dict[str, dict] = {}
        timestamp_str = datetime.now().isoformat()

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

        # DIAGNOSTIKA – přidáme do weather entity, pokud existuje
        if "weather" in result:
            result["weather"]["attributes"]["history_queue_length"] = self._diag_queue_length
            result["weather"]["attributes"]["history_worker_running"] = self._diag_worker_running
            result["weather"]["attributes"]["history_missing_count"] = self._diag_missing_count
            result["weather"]["attributes"]["history_last_batch_size"] = self._diag_last_batch_size
            result["weather"]["attributes"]["history_last_write_ts"] = (
                self._diag_last_write_ts.isoformat() if self._diag_last_write_ts else None
            )

        for api_key, value in raw.items():
            if api_key in (
                "Datum",
                "SrazkyDen",
                "LokalitaStanice",
                "DoplCidlaJson",
                "Historie",
                "Webkamera",
            ):
                continue

            already_mapped = any(
                m["api_key"] == api_key for m in SENSOR_DEFINITIONS.values()
            )
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
                {
                    "order": meta["order"],
                    "color": meta["color"],
                    "style": "smooth",
                    "visible": True,
                },
            )

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

        return result

    # -------------------------------------------------------------------------
    # Rolling 24h statistics (původní logika – může být ignorována frontendem)
    # -------------------------------------------------------------------------

    def _update_rolling_stats(self, data: dict[str, dict]):
        now = datetime.now()
        threshold = now - timedelta(hours=24)

        for sid, payload in data.items():
            value = payload["value"]
            if not isinstance(value, (int, float)):
                continue

            sensor_series = self._rolling_history.setdefault(sid, [])
            sensor_series.append((now, float(value)))

            self._rolling_history[sid] = [
                pt for pt in sensor_series if pt[0] >= threshold
            ]
            current_series = self._rolling_history[sid]

            values_only = [pt[1] for pt in current_series]
            if values_only and sid != "vitr_smer":
                payload["attributes"]["min"] = min(values_only)
                payload["attributes"]["max"] = max(values_only)

            if sid == "vitr_smer" and values_only:
                sin_sum = 0.0
                cos_sum = 0.0
                angles = []

                for val in values_only:
                    angles.append(val)
                    rad = math.radians(val)
                    sin_sum += math.sin(rad)
                    cos_sum += math.cos(rad)

                count = len(values_only)
                avg_sin = sin_sum / count
                avg_cos = cos_sum / count

                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos))
                if avg_deg < 0:
                    avg_deg += 360.0

                rounded = [round(a / 22.5) * 22.5 % 360 for a in angles]
                mode_deg = Counter(rounded).most_common(1)[0][0]

                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                if 0.001 < r_vector < 1.0:
                    var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                else:
                    var_deg = 0.0

                payload["attributes"]["vitr_smer_avg"] = round(avg_deg, 1)
                payload["attributes"]["vitr_smer_mode"] = round(mode_deg, 1)
                payload["attributes"]["vitr_smer_var"] = round(min(var_deg, 180.0), 1)
