"""Constants for PočasíMeteo integration."""

from __future__ import annotations
from datetime import timedelta
from typing import Any

DOMAIN = "pocasimeteo"
DEFAULT_NAME = "PočasíMeteo"

# ------------------------------------------------------------
# Konfigurační klíče
# ------------------------------------------------------------

CONF_STATION = "station_name"
CONF_API_KEY = "api_key"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_FORECAST_ENTITY_ID = "forecast_entity_id"
CONF_SENSORS = "sensors"

# ------------------------------------------------------------
# API endpoint – správná verze pro coordinator
# ------------------------------------------------------------

API_URL_BASE = "https://ext.pocasimeteo.cz/ms/api/weather"

DEFAULT_UPDATE_INTERVAL_MINUTES = 5
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

# ------------------------------------------------------------
# Styl grafů
# ------------------------------------------------------------

GRAPH_STYLE_SMOOTH = "smooth"
GRAPH_STYLE_STEPPED = "stepped"

STEPPED_SENSOR_IDS = [
    "vitr_rychlost",
    "vitr_narazy",
    "vitr_smer",
    "intenzita_srazek",
    "srazky_den",
]

# ------------------------------------------------------------
# Definice senzorů (kompletní metadata)
# ------------------------------------------------------------

SENSOR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "teplota_vnejsi": {
        "name": "Teplota venkovní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "primary",
        "order": 1,
        "api_key": "TeplotaVnejsi",
        "color": "#ff6b3d",
    },
    "vlhkost_vnejsi": {
        "name": "Vlhkost venkovní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "primary",
        "order": 2,
        "api_key": "VlhkostVnejsi",
        "color": "#1e88e5",
    },
    "tlak_relativni": {
        "name": "Tlak relativní",
        "unit": "hPa",
        "icon": "mdi:gauge",
        "type": "primary",
        "order": 3,
        "api_key": "TlakRel",
        "color": "#8e24aa",
    },
    "intenzita_srazek": {
        "name": "Intenzita srážek",
        "unit": "mm/h",
        "icon": "mdi:weather-rainy",
        "type": "primary",
        "order": 4,
        "api_key": "SrazkyIntenzita",
        "color": "#0288d1",
    },
    "vitr_rychlost": {
        "name": "Vítr rychlost",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 5,
        "api_key": "Vitr",
        "color": "#43a047",
    },
    "vitr_narazy": {
        "name": "Vítr nárazy",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 6,
        "api_key": "VitrNarazy",
        "color": "#2e7d32",
    },
    "vitr_smer": {
        "name": "Vítr směr",
        "unit": "°",
        "icon": "mdi:compass",
        "type": "primary",
        "order": 7,
        "api_key": "VitrSmer",
        "color": "#009688",
    },
    "slunecni_zareni": {
        "name": "Sluneční záření",
        "unit": "W/m²",
        "icon": "mdi:white-balance-sunny",
        "type": "primary",
        "order": 8,
        "api_key": "SlunZareni",
        "color": "#ffb300",
    },
    "uv_index": {
        "name": "UV index",
        "unit": "",
        "icon": "mdi:sun-wireless",
        "type": "primary",
        "order": 9,
        "api_key": "UVindex",
        "color": "#fdd835",
    },
    "teplota_vnitrni": {
        "name": "Teplota vnitřní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "secondary",
        "order": 100,
        "api_key": "TeplotaVnitrni",
        "color": "#ffa86b",
    },
    "vlhkost_vnitrni": {
        "name": "Vlhkost vnitřní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "secondary",
        "order": 101,
        "api_key": "VlhkostVnitrni",
        "color": "#64b5f6",
    },
}

# ------------------------------------------------------------
# Default options pro OptionsFlow a frontend kartu
# ------------------------------------------------------------

DEFAULT_SENSOR_OPTIONS = {
    sensor_id: {
        "order": meta["order"],
        "color": meta["color"],
        "style": GRAPH_STYLE_STEPPED if sensor_id in STEPPED_SENSOR_IDS else GRAPH_STYLE_SMOOTH,
        "visible": True,
    }
    for sensor_id, meta in SENSOR_DEFINITIONS.items()
}

DEFAULT_OPTIONS = {
    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL_MINUTES,
    CONF_FORECAST_ENTITY_ID: "",
    CONF_SENSORS: DEFAULT_SENSOR_OPTIONS,
}

# ------------------------------------------------------------
# Fallback metadata pro dynamické senzory
# ------------------------------------------------------------

def get_dynamic_sensor_meta(api_key: str) -> dict:
    """Fallback metadata for dynamically discovered sensors."""
    sid = api_key.lower()
    return {
        "api_key": api_key,
        "name": sid.replace("_", " ").capitalize(),
        "unit": None,
        "icon": "mdi:chart-line",
        "type": "secondary",
        "order": 999,
        "color": "#3b82f6",
    }
