"""Data update coordinator for PočasíMeteo."""

import logging
from datetime import timedelta

import aiohttp
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_STATION,
    CONF_API_KEY,
    API_URL_TEMPLATE,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator pro načítání dat z nové API PočasíMeteo."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Inicializace koordinátoru."""
        self.hass = hass
        self.station_name = entry.data[CONF_STATION]
        self.api_key = entry.data[CONF_API_KEY]

        # Sestav API URL
        self.api_url = API_URL_TEMPLATE.format(api_key=self.api_key)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.station_name}",
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self):
        """Načti data z API PočasíMeteo."""
        _LOGGER.debug("Fetching PočasíMeteo data from %s", self.api_url)

        try:
            async with async_timeout.timeout(20):
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.api_url, timeout=15) as response:
                        if response.status != 200:
                            raise UpdateFailed(
                                f"API returned HTTP {response.status}"
                            )

                        raw = await response.json()

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

        # --- Rozbalení formátu API -----------------------------------------

        # 1) objekt s timestampem + data[]
        if isinstance(raw, dict) and "data" in raw:
            records = raw["data"]

        # 2) jeden záznam se Zprava: "Posilame data"
        elif isinstance(raw, dict) and raw.get("Zprava") == "Posilame data":
            records = [raw]

        # 3) přímo pole záznamů
        elif isinstance(raw, list):
            records = raw

        else:
            raise UpdateFailed("Neplatný formát dat z API PočasíMeteo")

        if not records:
            raise UpdateFailed("API returned empty dataset")

        # --- Oddělení metadata záznamu -------------------------------------

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
            raise UpdateFailed("API returned metadata but no measurements")

        # --- Vyber poslední měření jako aktuální ---------------------------

        current = measurements[-1]

        # --- Sestav strukturu pro entity -----------------------------------

        data = {
            "station_name": self.station_name,
            "meta": meta,
            "raw": current,
            "temperature": current.get("TeplotaVnejsi"),
            "humidity": current.get("VlhkostVnejsi"),
            "pressure": current.get("TlakRel"),
            "wind_speed": current.get("Vitr"),
            "wind_gust": current.get("VitrNarazy"),
            "wind_bearing": current.get("VitrSmer"),
            "solar_radiation": current.get("SlunZareni"),
            "uv_index": current.get("UVindex"),
            "rain_daily": current.get("SrazkyDen"),
            "rain_intensity": current.get("rainIntensity")
                or current.get("calculatedRain"),
            "timestamp": current.get("Datum"),
        }

        _LOGGER.debug("Processed PočasíMeteo data: %s", data)

        return data
