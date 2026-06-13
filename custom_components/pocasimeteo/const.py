"""Constants for PočasíMeteo integration."""

from datetime import timedelta

DOMAIN = "pocasimeteo"

# Konfigurační klíče
CONF_STATION = "station_name"      # Název stanice (pro zobrazení)
CONF_API_KEY = "api_key"           # API klíč meteostanice
CONF_UPDATE_INTERVAL = "update_interval"    # Interval aktualizace dat (pro senzory)

# API endpoint pro nové PočasíMeteo API
API_URL_TEMPLATE = (
    "https://ext.pocasimeteo.cz/ms/api/weather?KlicApi={api_key}"
)

# Interval aktualizace dat
UPDATE_INTERVAL = timedelta(minutes=5)

# Výchozí hodnoty pro případné rozšíření
DEFAULT_NAME = "PočasíMeteo"
