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
    """Coordinator responsible for fetching and normalizing PočasíMeteo data."""

    async def _history_exists(self, entity_id: str, ts: datetime) -> bool:
        """Check if recorder already contains a state for given timestamp."""
        rec = get_instance(self.hass)
        with rec.get_session() as session:
            q = select(States).where(
                States.entity_id == entity_id,
                States.last_changed == ts
            )
            return session.execute(q).first() is not None

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        """Insert a historical state into recorder DB."""
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

    async def _import_history(self, measurements: list[dict]):
        """Import full 5-minute history from API into recorder."""
        for m in measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue

            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))

            # Projdeme všechny veličiny
            for key, value in m.items():
                if key in ("Datum", "LokalitaStanice", "DoplCidlaJson"):
                    continue

                entity_id = f"sensor.pocasimeteo_{key.lower()}"

                # Konverze hodnoty
                v = self._to_float(value) if key in self.FLOAT_KEYS else value
                if v is None:
                    continue

                # Pokud záznam neexistuje → vložíme
                if not await self._history_exists(entity_id, ts):
                    await self._insert_history_point(entity_id, v, ts)

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

        # --- Import historie měření z API ---
        history_payload = None

        # API někdy posílá historii v poli "DoplCidlaJson"
        if isinstance(raw.get("DoplCidlaJson"), dict):
            history_payload = raw["DoplCidlaJson"].get("Historie")

        # API někdy posílá historii přímo v poli "Historie"
        if history_payload is None and isinstance(raw.get("Historie"), list):
            history_payload = raw["Historie"]

        # Pokud máme platnou historii → doplníme ji do Recorderu
        if isinstance(history_payload, list) and len(history_payload) > 0:
            try:
                await self._import_history(history_payload)
            except Exception as hist_err:
                _LOGGER.warning(f"Import historie PočasíMeteo selhal: {hist_err}")

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
        """Compute min/max and incremental daily wind statistics securely using Czech IDs."""
        today = date.today()

        # Pokud nastal nový den, kompletně resetujeme denní paměť statistik
        if "_date" not in self._daily_stats or self._daily_stats["_date"] != today:
            self._daily_stats = {
                "_date": today,
                "vitr_smer_angles": [], # Pole pro ukládání historie úhlů pro přesný modus
                "vitr_smer_sin_sum": 0.0,
                "vitr_smer_cos_sum": 0.0,
                "vitr_smer_count": 0
            }

        for sid, payload in data.items():
            value = payload["value"]
            if not isinstance(value, (int, float)):
                continue

            # Standardní celoplošný denní min/max pro všechny číselné senzory
            stats = self._daily_stats.setdefault(sid, {"min": value, "max": value})
            if value < stats["min"]:
                stats["min"] = value
            if value > stats["max"]:
                stats["max"] = value

            payload["attributes"]["min"] = stats["min"]
            payload["attributes"]["max"] = stats["max"]

            # SPECIÁLNÍ UKÁZKOVÁ INKREMENTÁLNÍ MATEMATIKA PRO SMĚR VĚTRU
            if sid == "vitr_smer":
                import math
                from collections import Counter

                # 1. Výpočet průměru (Vektorový průměr úhlů pomocí sinu a kosinu)
                rad = math.radians(value)
                self._daily_stats["vitr_smer_sin_sum"] += math.sin(rad)
                self._daily_stats["vitr_smer_cos_sum"] += math.cos(rad)
                self._daily_stats["vitr_smer_count"] += 1

                avg_sin = self._daily_stats["vitr_smer_sin_sum"] / self._daily_stats["vitr_smer_count"]
                avg_cos = self._daily_stats["vitr_smer_cos_sum"] / self._daily_stats["vitr_smer_count"]
                
                avg_deg = math.degrees(math.atan2(avg_sin, avg_cos))
                if avg_deg < 0:
                    avg_deg += 360.0
                
                # 2. Výpočet modusu (Nejčastější hodnota za dnešek zaokrouhlená na světové směry)
                self._daily_stats["vitr_smer_angles"].append(value)
                # Zaokrouhlíme na nejbližších 22.5 stupně pro stabilní určení dominantního směru
                rounded_angles = [round(a / 22.5) * 22.5 % 360 for a in self._daily_stats["vitr_smer_angles"]]
                occurence_count = Counter(rounded_angles)
                mode_deg = occurence_count.most_common(1)[0][0]

                # 3. Výpočet rozptylu variance (Kruhová směrodatná odchylka)
                # Vzorec: R = sqrt(avg_sin^2 + avg_cos^2). Rozptyl = sqrt(-2 * ln(R)) v radiánech.
                r_vector = math.sqrt(avg_sin**2 + avg_cos**2)
                if r_vector > 0.001 and r_vector < 1.0:
                    var_deg = math.degrees(math.sqrt(-2.0 * math.log(r_vector)))
                else:
                    var_deg = 0.0

                # Zápis hotových denních statistik přímo do atributů senzoru pro Lovelace kartu
                payload["attributes"]["vitr_smer_avg"] = round(avg_deg, 1)
                payload["attributes"]["vitr_smer_mode"] = round(mode_deg, 1)
                payload["attributes"]["vitr_smer_var"] = round(min(var_deg, 180.0), 1)
