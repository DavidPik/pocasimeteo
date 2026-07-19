"""Constants for PočasíMeteo integration."""

from __future__ import annotations

from datetime import timedelta

# ---------------------------------------------------------------------------
# Basic integration identification
# ---------------------------------------------------------------------------

DOMAIN = "pocasimeteo"
DEFAULT_NAME = "PočasíMeteo"

# ---------------------------------------------------------------------------
# Configuration keys
# ---------------------------------------------------------------------------

CONF_STATION = "station_name"          # Weather station name
CONF_API_KEY = "api_key"               # PočasíMeteo API key
CONF_UPDATE_INTERVAL = "update_interval"  # Data update interval (minutes)

# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

API_URL_TEMPLATE = "https://ext.pocasimeteo.cz/ms/api/weather?KlicApi={api_key}"

# Default polling interval (used as fallback, UI still controls actual value)
DEFAULT_UPDATE_INTERVAL_MINUTES = 5
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

# ---------------------------------------------------------------------------
# Sensor model
#
# All structural definitions for sensors live here. Other modules only
# consume this metadata and never hard‑code IDs, names, units or types.
#
# Fields:
#   id        – internal sensor ID used in options and entity_id suffix
#   name      – human‑readable name (Czech, for UI)
#   unit      – unit of measurement
#   icon      – Material Design Icon name
#   type      – "primary" or "secondary" (for card layout)
#   order     – default ordering index within its group
#   api_key   – key name in PočasíMeteo API JSON payload
# ---------------------------------------------------------------------------

SENSOR_DEFINITIONS: dict[str, dict[str, object]] = {
    # -----------------------------------------------------------------------
    # Primary sensors (main outdoor metrics)
    # -----------------------------------------------------------------------
    "TeplotaVnejsi": {
        "id": "TeplotaVnejsi",
        "name": "Teplota venkovní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "primary",
        "order": 1,
        "api_key": "TeplotaVnejsi",
    },
    "VlhkostVnejsi": {
        "id": "VlhkostVnejsi",
        "name": "Vlhkost venkovní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "primary",
        "order": 2,
        "api_key": "VlhkostVnejsi",
    },
    "TlakRel": {
        "id": "TlakRel",
        "name": "Tlak relativní",
        "unit": "hPa",
        "icon": "mdi:gauge",
        "type": "primary",
        "order": 3,
        "api_key": "TlakRel",
    },
    "SrazkyIntenzita": {
        "id": "SrazkyIntenzita",
        "name": "Srážky intenzita",
        "unit": "mm/h",
        "icon": "mdi:weather-rainy",
        "type": "primary",
        "order": 4,
        # API still uses RainIntensity – we map it here centrally
        "api_key": "RainIntensity",
    },
    "VitrRychlost": {
        "id": "VitrRychlost",
        "name": "Vítr rychlost",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 5,
        "api_key": "Vitr",
    },
    "VitrNarazy": {
        "id": "VitrNarazy",
        "name": "Vítr nárazy",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 6,
        "api_key": "VitrNarazy",
    },
    "VitrSmer": {
        "id": "VitrSmer",
        "name": "Vítr směr",
        "unit": "°",
        "icon": "mdi:compass",
        "type": "primary",
        "order": 7,
        "api_key": "VitrSmer",
    },
    "SlunZareni": {
        "id": "SlunZareni",
        "name": "Sluneční záření",
        "unit": "W/m²",
        "icon": "mdi:white-balance-sunny",
        "type": "primary",
        "order": 8,
        "api_key": "SlunZareni",
    },
    "UVIndex": {
        "id": "UVIndex",
        "name": "UV index",
        "unit": "",
        "icon": "mdi:sun-wireless",
        "type": "primary",
        "order": 9,
        "api_key": "UVindex",
    },

    # -----------------------------------------------------------------------
    # Secondary sensors (indoor metrics)
    # -----------------------------------------------------------------------
    "TeplotaVnitrni": {
        "id": "TeplotaVnitrni",
        "name": "Teplota vnitřní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "secondary",
        "order": 100,
        "api_key": "TeplotaVnitrni",
    },
    "VlhkostVnitrni": {
        "id": "VlhkostVnitrni",
        "name": "Vlhkost vnitřní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "secondary",
        "order": 101,
        "api_key": "VlhkostVnitrni",
    },
}

# ---------------------------------------------------------------------------
# Derived defaults used by config_flow / options_flow
# ---------------------------------------------------------------------------

DEFAULT_PRIMARY_SENSOR_IDS: list[str] = [
    sid for sid, meta in SENSOR_DEFINITIONS.items() if meta["type"] == "primary"
]

DEFAULT_SECONDARY_SENSOR_IDS: list[str] = [
    sid for sid, meta in SENSOR_DEFINITIONS.items() if meta["type"] == "secondary"
]

DEFAULT_ALL_SENSOR_IDS: list[str] = DEFAULT_PRIMARY_SENSOR_IDS + DEFAULT_SECONDARY_SENSOR_IDS

# Default full sensors structure for initial options (used by config_flow)
DEFAULT_SENSORS_OPTIONS: list[dict[str, object]] = [
    {
        "id": sid,
        "type": SENSOR_DEFINITIONS[sid]["type"],
        "order": SENSOR_DEFINITIONS[sid]["order"],
    }
    for sid in DEFAULT_ALL_SENSOR_IDS
]
