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

# Výchozí interval aktualizace
UPDATE_INTERVAL = timedelta(minutes=5)

# ------------------------------------------------------------
# Výchozí seznam senzorů (uživatel může rozšířit v OptionsFlow)
# ------------------------------------------------------------

DEFAULT_SENSORS = [
    # Primární senzory
    "TeplotaVnejsi",
    "VlhkostVnejsi",
    "TlakRel",
    "Vitr",
    "VitrSmer",
    "UVindex",
    "SrazkyIntenzita",

    # Doplňkové senzory
    "TeplotaVnitrni",
    "VlhkostVnitrni",
]

# ------------------------------------------------------------
# Výchozí typy senzorů (uživatel může změnit v OptionsFlow)
# ------------------------------------------------------------

DEFAULT_SENSOR_TYPES = {
    "TeplotaVnejsi": "primary",
    "VlhkostVnejsi": "primary",
    "TlakRel": "primary",
    "Vitr": "primary",
    "VitrSmer": "primary",
    "UVindex": "primary",
    "SrazkyIntenzita": "primary",

    "TeplotaVnitrni": "secondary",
    "VlhkostVnitrni": "secondary",
}

# ------------------------------------------------------------
# Výchozí pořadí senzorů (uživatel může změnit v OptionsFlow)
# ------------------------------------------------------------

DEFAULT_SENSOR_ORDER = {
    "TeplotaVnejsi": 1,
    "VlhkostVnejsi": 2,
    "TlakRel": 3,
    "Vitr": 4,
    "VitrSmer": 5,
    "UVindex": 6,
    "SrazkyIntenzita": 7,

    "TeplotaVnitrni": 21,
    "VlhkostVnitrni": 22,
}
