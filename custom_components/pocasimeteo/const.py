"""Constants for PočasíMeteo integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

DOMAIN = "pocasimeteo"
DEFAULT_NAME = "PočasíMeteo"

CONF_STATION = "station_name"          
CONF_API_KEY = "api_key"               
CONF_UPDATE_INTERVAL = "update_interval"  

API_URL_BASE = "https://pocasimeteo.cz"

DEFAULT_UPDATE_INTERVAL_MINUTES = 5
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

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

def get_dynamic_sensor_meta(key: str) -> dict[str, Any]:
    """Fallback metadata pro neznámá čidla."""
    key_lower = key.lower()
    name = key
    unit = ""
    icon = "mdi:eye"
    sensor_type = "secondary"
    order = 200
    color = "#7e57c2"

    if key_lower.startswith("te") or "temp" in key_lower:
        name = f"Teplota {key.replace('Te', '')}"
        unit = "°C"
        icon = "mdi:thermometer"
        color = "#ff6b3d"
    elif key_lower.startswith("vl") or "hum" in key_lower:
        name = f"Vlhkost {key.replace('Vl', '')}"
        unit = "%"
        icon = "mdi:water-percent"
        color = "#1e88e5"
    elif "co2" in key_lower:
        name = "CO₂"
        unit = "ppm"
        icon = "mdi:molecule-co2"
        order = 110
        color = "#6d4c41"

    return {
        "name": name,
        "unit": unit,
        "icon": icon,
        "type": sensor_type,
        "order": order,
        "api_key": key,
        "color": color,
    }
