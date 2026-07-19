"""Constants for PočasíMeteo integration."""

from datetime import timedelta

# ------------------------------------------------------------
# Základní identifikace integrace
# ------------------------------------------------------------

DOMAIN = "pocasimeteo"
DEFAULT_NAME = "PočasíMeteo"

# ------------------------------------------------------------
# Konfigurační klíče
# ------------------------------------------------------------

CONF_STATION = "station_name"          # Název meteostanice
CONF_API_KEY = "api_key"               # API klíč meteostanice
CONF_UPDATE_INTERVAL = "update_interval"  # Interval aktualizace dat (minuty)

# ------------------------------------------------------------
# API endpoint pro PočasíMeteo
# ------------------------------------------------------------

API_URL_TEMPLATE = (
    "https://ext.pocasimeteo.cz/ms/api/weather?KlicApi={api_key}"
)

# ------------------------------------------------------------
# Výchozí seznamy senzorů (používá config_flow + options_flow)
# ------------------------------------------------------------

DEFAULT_PRIMARY_SENSORS = [
    "TeplotaVnejsi",
    "VlhkostVnejsi",
    "TlakRel",
    "SlunZareni",
    "Vitr",
    "VitrNarazy",
    "VitrSmer",
    "RainIntensity",
]

DEFAULT_SECONDARY_SENSORS = [
    "TeplotaVnitrni",
    "VlhkostVnitrni",
]
