"""Constants for PočasíMeteo integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

DOMAIN = "pocasimeteo"
DEFAULT_NAME = "PočasíMeteo"

CONF_STATION = "station_name"          
CONF_API_KEY = "api_key"               
CONF_UPDATE_INTERVAL = "update_interval"  

API_URL_BASE = "https://ext.pocasimeteo.cz/ms/api/weather"

DEFAULT_UPDATE_INTERVAL_MINUTES = 5
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

# Klíče (ID senzorů) jsou nyní malými písmeny, přesně podle vaší JS karty
SENSOR_DEFINITIONS: dict[str, dict[str, Any]] = {
    "teplotavnejsi": {
        "name": "Teplota venkovní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "primary",
        "order": 1,
        "api_key": "TeplotaVnejsi",
    },
    "vlhkostvnejsi": {
        "name": "Vlhkost venkovní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "primary",
        "order": 2,
        "api_key": "VlhkostVnejsi",
    },
    "tlakrel": {
        "name": "Tlak relativní",
        "unit": "hPa",
        "icon": "mdi:gauge",
        "type": "primary",
        "order": 3,
        "api_key": "TlakRel",
    },
    "rainintensity": {
        "name": "Intenzita srážek",
        "unit": "mm/h",
        "icon": "mdi:weather-rainy",
        "type": "primary",
        "order": 4,
        "api_key": "SrazkyIntenzita",  # Interní klíč, který vygeneruje koordinátor
    },
    "vitr": {
        "name": "Vítr",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 5,
        "api_key": "Vitr",
    },
    "vitrnarazy": {
        "name": "Nárazy větru",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "type": "primary",
        "order": 6,
        "api_key": "VitrNarazy",
    },
    "vitrsmer": {
        "name": "Směr větru",
        "unit": "°",
        "icon": "mdi:compass",
        "type": "primary",
        "order": 7,
        "api_key": "VitrSmer",
    },
    "slunzareni": {
        "name": "Sluneční záření",
        "unit": "W/m²",
        "icon": "mdi:white-balance-sunny",
        "type": "primary",
        "order": 8,
        "api_key": "SlunZareni",
    },
    "uvindex": {
        "name": "UV index",
        "unit": "",
        "icon": "mdi:sun-wireless",
        "type": "primary",
        "order": 9,
        "api_key": "UVindex",
    },
    "teplotavnitrni": {
        "name": "Teplota vnitřní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "type": "secondary",
        "order": 100,
        "api_key": "TeplotaVnitrni",
    },
    "vlhkostvnitrni": {
        "name": "Vlhkost vnitřní",
        "unit": "%",
        "icon": "mdi:water-percent",
        "type": "secondary",
        "order": 101,
        "api_key": "VlhkostVnitrni",
    },
}

def get_dynamic_sensor_meta(key: str) -> dict[str, Any]:
    """Sestaví metadata pro dynamické senzory (např. co2, pm1, pm2 apod.)."""
    key_lower = key.lower()
    
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
        order = 110
    elif "pm" in key_lower:
        name = f"Polétavý prach {key.upper()}"
        unit = "µg/m³"
        icon = "mdi:air-filter"
        order = 120

    return {
        "name": name,
        "unit": unit,
        "icon": icon,
        "type": sensor_type,
        "order": order,
        "api_key": key,
    }
