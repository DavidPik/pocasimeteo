"""Data update coordinator for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
import math
import json
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
    SENSOR_DEFINITIONS,
    DEFAULT_SENSOR_OPTIONS,
    get_dynamic_sensor_meta,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Koordinátor odpovědný za stahování dat, plnění mezer v historii databáze
    a výpočet klouzavých 24h statistik pro potřeby frontendové karty.
    """

    # -------------------------------------------------------------------------
    # Recorder helpers (Bezpečný asynchronní zápis kompatibilní s moderním HA)
    # -------------------------------------------------------------------------

    async def _history_exists(self, entity_id: str, ts: datetime) -> bool:
        """Ověří v DB existenci bodu. Spouští se bezpečně v executor jobu."""
        def _check():
            rec = get_instance(self.hass)
            with rec.get_session() as session:
                q = select(States).where(
                    States.entity_id == entity_id,
                    States.last_changed == ts
                )
                return session.execute(q).first() is not None

        # ODCHYLKA/BEZPEČNOST: Databázové dotazy nesmí běžet přímo v event loopu, 
        # jinak by způsobily mikro-zárazy celého HA Green. Delegujeme je do vlákna na pozadí.
        return await self.hass.async_add_executor_job(_check)

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        """Bezpečně vloží historický stav se správným provázáním cizích klíčů DB."""
        def _insert():
            rec = get_instance(self.hass)
            with rec.get_session() as session:
                # 1. Zjistíme nebo vytvoříme metadata_id pro danou entitu (vyžadováno od HA 2023.x+)
                meta_row = session.execute(
                    select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                ).scalar_one_or_none()
                
                if not meta_row:
                    meta_row = StatesMeta(entity_id=entity_id)
                    session.add(meta_row)
                    session.flush()
                
                metadata_id = meta_row.metadata_id

                # 2. Vytvoříme prázdné atributy, které vyžaduje schéma
                attr_row = session.execute(
                    select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
                ).scalar_one_or_none()
                
                if not attr_row:
                    attr_row = StateAttributes(shared_attrs="{}")
                    session.add(attr_row)
                    session.flush()
                
                attributes_id = attr_row.attributes_id

                # 3. Zapíšeme samotný historický stav
                row = States(
                    entity_id=entity_id,
                    metadata_id=metadata_id,
                    attributes_id=attributes_id,
                    state=str(value),
                    last_changed=ts,
                    last_updated=ts
                )
                session.add(row)
                session.commit()

        await self.hass.async_add_executor_job(_insert)
    
    # -------------------------------------------------------------------------
    # Import full 5-minute history from API
    # -------------------------------------------------------------------------

    async def _import_history(self, measurements: list[dict]):
        """Zpracuje historii z JSONu API a doplní chybějící body do databáze."""
        sorted_measurements = sorted(
            measurements,
            key=lambda m: datetime.fromisoformat(m["Datum"].replace("Z", "+00:00"))
        )

        previous_rain = None
        previous_ts = None

        # Výpočet derivace intenzity srážek (mm/h)
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

        if sorted_measurements:
            self._latest_rain_intensity = sorted_measurements[-1].get("SrazkyIntenzita", 0.0)

        # Samotný bezpečný import do DB se sjednocením klíčů podle const.py
        station_prefix = self.entry.title.lower().replace(" ", "_")

        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))

            # Převodní slovník z API klíčů na interní ID senzorů z const.py
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
                "vlhkostvnitrni": "vlhkost_vnitrni"
            }

            for key, value in m.items():
                if key in ("Datum", "LokalitaStanice", "DoplCidlaJson"):
                    continue

                # Vyhledáváme v mapování s převedením klíče na malá písmena
                key_lower = key.lower()
                internal_sid = api_to_internal_mapping.get(key_lower, key_lower)
                entity_id = f"sensor.{station_prefix}_{internal_sid}"

                try:
                    v = float(value)
                except Exception:
                    v = value

                if v is None:
                    continue

                # Zkontrolujeme a zapíšeme bod pod správným systémovým entity_id
                if not await self._history_exists(entity_id, ts):
                    await self._insert_history_point(entity_id, v, ts)

    # -------------------------------------------------------------------------
    # Coordinator initialization
    # -------------------------------------------------------------------------

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry

        update_interval_minutes = entry.options.get(
            CONF_UPDATE_INTERVAL,
            entry.data.get(CONF_UPDATE_INTERVAL, 5)
        )

        self._sensor_options = entry.options.get(CONF_SENSORS, DEFAULT_SENSOR_OPTIONS)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=update_interval_minutes),
        )

        # ARCHITEKTURA FRONTENDU / PAMĚŤ: Zde budeme držet čistou časovou řadu bodů 
        # (hodnota, timestamp) za posledních 24 hodin, ze které průběžně počítáme klouzavé statistiky.
        self._rolling_history: dict[str, list[tuple[datetime, float]]] = {}
        self._latest_rain_intensity: float = 0.0
        self.station_metadata = {}
        self.sensors_payload = {}

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

        # Zpracování metadat stanice a extrakce payloadu počasí
        if isinstance(raw, list) and len(raw) > 0:
            # První prvek pole obsahuje metadata stanice
            meta_payload = raw[0]
            if isinstance(meta_payload, dict):
                self.station_metadata["station_name"] = self.entry.data.get(CONF_STATION)
                self.station_metadata["lokalita_stanice"] = meta_payload.get("LokalitaStanice")
                if "Webkamera" in meta_payload and isinstance(meta_payload["Webkamera"], dict):
                    self.station_metadata["webcamera_url"] = meta_payload["Webkamera"].get("UrlWebcam")

            # Extrakce samotného počasí z pole
            if len(raw) > 1 and isinstance(raw[1], dict) and "Datum" in raw[1]:
                raw = raw[1]
            elif isinstance(raw[0], dict) and "Datum" in raw[0]:
                raw = raw[0]
            else:
                raise UpdateFailed("API response structure valid, but weather payload missing")

        # Uložíme denní srážky do metadata, aby je weather entita mohla předat frontendové kartě
        if "SrazkyDen" in raw:
            self.station_metadata["srazky_den"] = raw["SrazkyDen"]

        # Import historie do DB (vyplnění mezer po výpadku)
        history_payload = None

        history_payload = None

        # 1) Pokus o načtení historie z DoplCidlaJson
        dopl = raw.get("DoplCidlaJson")
        if isinstance(dopl, str):
            try:
                dopl = json.loads(dopl)
            except:
                dopl = None

        if isinstance(dopl, dict):
            history_payload = dopl.get("Historie")

        # 2) Fallback – API posílá historii přímo
        if history_payload is None:
            history_payload = raw.get("Historie")

        if isinstance(history_payload, list) and len(history_payload) > 0:
            try:
                await self._import_history(history_payload)
            except Exception as hist_err:
                _LOGGER.warning("Import historie PočasíMeteo selhal: %s", hist_err)

        # Fallback výpočet intenzity srážek, pokud API neposílá historii
        if self._latest_rain_intensity == 0.0 and "SrazkyDen" in raw:
            try:
                # Získáme poslední hodnotu z rolling history
                rain_series = self._rolling_history.get("srazky_den", [])
                if len(rain_series) >= 1:
                    prev_val = rain_series[-1][1]
                    curr_val = float(raw["SrazkyDen"])
                    delta = curr_val - prev_val
                    if delta > 0:
                        # 5 minut = 0.0833 h
                        self._latest_rain_intensity = round(delta / 0.0833, 2)
            except Exception as e:
                _LOGGER.debug("Fallback intensity calculation failed: %s", e)

        raw["SrazkyIntenzita"] = self._latest_rain_intensity

        # Normalizace a klouzavé 24h statistiky
        normalized = self._normalize_data(raw)
        self._update_rolling_stats(normalized)

        self.sensors_payload = normalized
        return normalized

    # -------------------------------------------------------------------------
    # Normalize API payload into HA sensor format
    # -------------------------------------------------------------------------

    def _normalize_data(self, raw: dict) -> dict[str, dict[str, any]]:
        """Přetransformuje syrový JSON z API do standardizované HA struktury."""
        result: dict[str, dict] = {}
        timestamp_str = datetime.now().isoformat()

        # Zpracování staticky definovaných senzorů z const.py
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

        # Dynamické objevování čidel (Fallback)
        for api_key, value in raw.items():
            if api_key in ("Datum", "SrazkyDen", "LokalitaStanice", "DoplCidlaJson", "Historie", "Webkamera"):
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

            opts = self._sensor_options.get(sid, {
                "order": meta["order"],
                "color": meta["color"],
                "style": "smooth",
                "visible": True,
            })

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
    # Rolling 24h statistics
    # -------------------------------------------------------------------------

    def _update_rolling_stats(self, data: dict[str, dict]):
        """Počítá statistiky ze striktně klouzavého 24h okna."""
        now = datetime.now()
        threshold = now - timedelta(hours=24)

        for sid, payload in data.items():
            value = payload["value"]
            if not isinstance(value, (int, float)):
                continue

            # Uložíme nový bod do časové řady v RAM koordinátoru
            sensor_series = self._rolling_history.setdefault(sid, [])
            sensor_series.append((now, float(value)))

            # ČIŠTĚNÍ PAMĚTI: Odstraníme z pole body starší než 24 hodin
            self._rolling_history[sid] = [pt for pt in sensor_series if pt[0] >= threshold]
            current_series = self._rolling_history[sid]

            # Výpočet základního Min/Max z klouzavého okna
            values_only = [pt[1] for pt in current_series]
            if values_only:
                current_min = min(values_only)
                current_max = max(values_only)
                
                # ARCHITEKTURA FRONTENDU: Vkládáme min/max do atributů, karta je kreslí do popisků pod grafem
                payload["attributes"]["min"] = current_min
                payload["attributes"]["max"] = current_max

            # Pokročilá kruhová statistika pro směr větru
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

                # Výpočet Modu (převládající směr) za 24h rozdělený po 22.5 stupních
                rounded = [round(a / 22.5) * 22.5 % 360 for a in angles]
                mode_deg = Counter(rounded).most_common(1)[0][0]

                # Výpočet úhlového rozptylu (Variance)
                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                if 0.001 < r_vector < 1.0:
                    var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                else:
                    var_deg = 0.0

                # ARCHITEKTURA FRONTENDU / ODCHYLKA: Tyto atributy jsou nestandardní.
                # Předáváme je přes senzor směru větru, aby si je Canvas prvek větrné růžice mohl 
                # okamžitě vytáhnout a nakreslit osy (průměr, modus, výseč rozptylu).
                payload["attributes"]["vitr_smer_avg"] = round(avg_deg, 1)
                payload["attributes"]["vitr_smer_mode"] = round(mode_deg, 1)
                payload["attributes"]["vitr_smer_var"] = round(min(var_deg, 180.0), 1)
