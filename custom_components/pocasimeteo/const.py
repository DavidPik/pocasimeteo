"""Constants for PočasíMeteo integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

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
# ---------------------------------------------------------------------------

SENSOR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "TeplotaVnejsi": {
        "name": "Teplota venkovní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "primary",
        "order": 1,
        "api_key": "TeplotaVnejsi",
    },
    "VlhkostVnejsi": {
        "name": "Vlhkost venkovní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "primary",
        "order": 2,
        "api_key": "VlhkostVnejsi",
    },
    "TlakRel": {
        "name": "Tlak relativní",
        "unit": "hPa",
        "icon": "mdi:gauge",
        "type": "primary",
        "order": 3,
        "api_key": "TlakRel",
    },
    "SrazkyIntenzita": {
        "name": "Srážky intenzita",
        "unit": "mm/h",
        "icon": "mdi:weather-rainy",
        "type": "primary",
        "order": 4,
        "api_key": "RainIntensity",
    },
    "VitrRychlost": {
        "name": "Vítr rychlost",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 5,
        "api_key": "Vitr",
    },
    "VitrNarazy": {
        "name": "Vítr nárazy",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 6,
        "api_key": "VitrNarazy",
    },
    "VitrSmer": {
        "name": "Vítr směr",
        "unit": "°",
        "icon": "mdi:compass",
        "type": "primary",
        "order": 7,
        "api_key": "VitrSmer",
    },
    "SlunZareni": {
        "name": "Sluneční záření",
        "unit": "W/m²",
        "icon": "mdi:white-balance-sunny",
        "type": "primary",
        "order": 8,
        "api_key": "SlunZareni",
    },
    "UVIndex": {
        "name": "UV index",
        "unit": "",
        "icon": "mdi:sun-wireless",
        "type": "primary",
        "order": 9,
        "api_key": "UVindex",
    },
    "TeplotaVnitrni": {
        "name": "Teplota vnitřní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "secondary",
        "order": 100,
        "api_key": "TeplotaVnitrni",
    },
    "VlhkostVnitrni": {
        "name": "Vlhkost vnitřní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "secondary",
        "order": 101,
        "api_key": "VlhkostVnitrni",
    },
}

# ---------------------------------------------------------------------------
# Dynamic Sensors Helper
# ---------------------------------------------------------------------------

def get_dynamic_sensor_meta(key: str) -> dict[str, Any]:
    """Sestaví metadata pro senzor, který není pevně definován v SENSOR_DEFINITIONS."""
    key_lower = key.lower()
    
    # Výchozí hodnoty
    name = key
    unit = ""
    icon = "mdi:eye"
    sensor_type = "secondary"
    order = 200

    if key_lower.startswith("te") or "temp" in key_lower:
        name = f"Teplota {key.replace('Te', '')}"
        unit = "°C"
        icon = "mdi:thermometer"
    elif key_lower.startswith("vl") or "hum" in key_lower:
        name = f"Vlhkost {key.replace('Vl', '')}"
        unit = "%"
        icon = "mdi:water-percent"
    elif "co2" in key_lower:
        name = "CO₂"
        unit = "ppm"
        icon = "mdi:molecule-co2"
    elif "pm" in key_lower:
        name = f"Polétavý prach {key.upper()}"
        unit = "µg/m³"
        icon = "mdi:air-filter"
    elif "press" in key_lower or "tlak" in key_lower:
        name = "Tlak vzduchu"
        unit = "hPa"
        icon = "mdi:gauge"

    return {
        "name": name,
        "unit": unit,
        "icon": icon,
        "type": sensor_type,
        "order": order,
        "api_key": key,
    }

# ---------------------------------------------------------------------------
# Derived lists for internal HA logic
# ---------------------------------------------------------------------------

DEFAULT_PRIMARY_SENSOR_IDS: list[str] = [
    sid for sid, meta in SENSOR_DEFINITIONS.items() if meta["type"] == "primary"
]

DEFAULT_SECONDARY_SENSOR_IDS: list[str] = [
    sid for sid, meta in SENSOR_DEFINITIONS.items() if meta["type"] == "secondary"
]

DEFAULT_ALL_SENSOR_IDS: list[str] = DEFAULT_PRIMARY_SENSOR_IDS + DEFAULT_SECONDARY_SENSOR_IDS
