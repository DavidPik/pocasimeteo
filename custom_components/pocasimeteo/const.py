"""Constants for PočasíMeteo integration."""

DOMAIN = "pocasimeteo"

API_URL_BASE = "https://ext.pocasimeteo.cz/ms/api/weather"

CONF_STATION = "station"
CONF_API_KEY = "api_key"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_FORECAST_ENTITY_ID = "forecast_entity_id"
CONF_SENSORS = "sensors"

GRAPH_STYLE_SMOOTH = "smooth"
GRAPH_STYLE_STEPPED = "stepped"

DEFAULT_OPTIONS = {
    CONF_UPDATE_INTERVAL: 5,
    CONF_FORECAST_ENTITY_ID: "",
}

# Základní sada senzorů – můžeš si ji upravit podle své stanice
DEFAULT_SENSOR_OPTIONS = {
    "teplota_vnejsi": {
        "order": 1,
        "color": "#f59e0b",
        "style": GRAPH_STYLE_SMOOTH,
        "visible": True,
    },
    "tlak": {
        "order": 2,
        "color": "#3b82f6",
        "style": GRAPH_STYLE_SMOOTH,
        "visible": True,
    },
    "vlhkost": {
        "order": 3,
        "color": "#10b981",
        "style": GRAPH_STYLE_SMOOTH,
        "visible": True,
    },
    "vitr_rychlost": {
        "order": 4,
        "color": "#6366f1",
        "style": GRAPH_STYLE_SMOOTH,
        "visible": True,
    },
    "vitr_narazy": {
        "order": 5,
        "color": "#ef4444",
        "style": GRAPH_STYLE_SMOOTH,
        "visible": True,
    },
    "srazky_den": {
        "order": 6,
        "color": "#0ea5e9",
        "style": GRAPH_STYLE_STEPPED,
        "visible": True,
    },
    "vitr_smer": {
        "order": 7,
        "color": "#22c55e",
        "style": GRAPH_STYLE_SMOOTH,
        "visible": True,
    },
}

# Statická definice senzorů pro platformu sensor
SENSOR_DEFINITIONS = {
    "teplota_vnejsi": {
        "name": "Teplota venkovní",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": "temperature",
        "state_class": "measurement",
        "type": "primary",
    },
    "tlak": {
        "name": "Tlak",
        "unit": "hPa",
        "icon": "mdi:gauge",
        "device_class": None,
        "state_class": "measurement",
        "type": "primary",
    },
    "vlhkost": {
        "name": "Vlhkost",
        "unit": "%",
        "icon": "mdi:water-percent",
        "device_class": "humidity",
        "state_class": "measurement",
        "type": "primary",
    },
    "vitr_rychlost": {
        "name": "Rychlost větru",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "device_class": None,
        "state_class": "measurement",
        "type": "secondary",
    },
    "vitr_narazy": {
        "name": "Nárazy větru",
        "unit": "m/s",
        "icon": "mdi:weather-windy",
        "device_class": None,
        "state_class": "measurement",
        "type": "secondary",
    },
    "srazky_den": {
        "name": "Srážky dnes",
        "unit": "mm",
        "icon": "mdi:weather-rainy",
        "device_class": None,
        "state_class": "total",
        "type": "secondary",
    },
    "vitr_smer": {
        "name": "Směr větru",
        "unit": "°",
        "icon": "mdi:compass",
        "device_class": None,
        "state_class": "measurement",
        "type": "secondary",
    },
}


def get_dynamic_sensor_meta(api_key: str) -> dict:
    """Fallback metadata for dynamicky objevené senzory."""
    sid = api_key.lower()
    return {
        "api_key": api_key,
        "name": sid.replace("_", " ").capitalize(),
        "unit": None,
        "icon": "mdi:chart-line",
        "device_class": None,
        "state_class": "measurement",
        "type": "secondary",
    }
