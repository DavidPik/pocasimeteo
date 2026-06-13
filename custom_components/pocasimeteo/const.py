"""Constants for the PočasíMeteo integration."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DOMAIN = "pocasimeteo"
CONF_STATION = "station"
CONF_MODEL = "model"
CONF_API_KEY = "api_key"

# Souřadnice a časové pásmo pro Prahu
PRAGUE_COORDINATES = {"latitude": 50.0755, "longitude": 14.4378}
PRAGUE_TIMEZONE = ZoneInfo("Europe/Prague")

# Příklady dostupných stanic (uživatel může zadat libovolnou stanici)
# Formát: název stanice odpovídající URL na pocasimeteo.cz
# Např.: praha-6-ruzyne, brno, ostrava, plzen, liberec, olomouc, atd.

# Dostupné modely předpovědi
# Format: "MODEL_NAME": {"label": "Display Label", "file": "API_filename.json"}
WEATHER_MODELS = {
    "MASTER": {"label": "MASTER", "file": "MASTER_data.json"},
    "ALADIN": {"label": "ALADIN", "file": "ALADIN_data.json"},
    "ICON": {"label": "ICONDE", "file": "ICON_data.json"},
    "COSMO": {"label": "ICONEU", "file": "COSMO_data.json"},
    "YRno": {"label": "YRno", "file": "YRno_data.json"},
    "GFS": {"label": "GFS", "file": "GFS_data.json"},
    "WRF": {"label": "WRF", "file": "WRF_data.json"},
}

# Backwards compatibility - pro selectory v config flow
WEATHER_MODELS_LABELS = {name: info["label"] for name, info in WEATHER_MODELS.items()}

# URL šablona pro API
API_URL_TEMPLATE = "https://ext.pocasimeteo.cz/ms/api/weather?KlicApi={api_key}"

# Aktualizační interval - 1 minutu po každé celé hodině (s malou náhodností pro distribuci zátěže)
# Pozn: Tento interval se ignoruje v coordinatoru, kde se místo toho nastavuje konkrétní čas
UPDATE_INTERVAL = timedelta(hours=1)

# Maximální věk dat v minutách (starší data jsou považována za zastaralá)
DATA_MAX_AGE_MINUTES = 90

# Interval pro zkrácený update pokud jsou data zastaralá (v minutách)
DATA_STALE_UPDATE_INTERVAL_MINUTES = 5

# Mapování API ikon na skutečné PNG soubory (s prefixem "a")
# Defaultní logika: "01" → "a01.png", "46" → "a46.png", atd.
# Bez speciálního mapování - mapování probíhá čistě v frontend kódu
ICON_CODE_MAP = {}

# Mapování kódů počasí na HA podmínky
CONDITION_MAP = {
    "01": "sunny",
    "02": "partlycloudy",
    "03": "cloudy",
    "04": "cloudy",
    "09": "rainy",
    "10": "rainy",
    "11": "lightning-rainy",
    "13": "snowy",
    "46": "rainy",
    "50": "fog",
}


def get_best_model_for_forecast(hours_ahead: int = 0) -> str:
    """
    Určí nejlepší model pro předpověď podle času dopředu.
    
    Args:
        hours_ahead: Kolik hodin dopředu předpovídáme
        
    Returns:
        Název nejlepšího modelu
    """
    if hours_ahead <= 6:
        # Krátkodobá předpověď (0-6h): ALADIN je nejlepší pro ČR
        return "ALADIN"
    elif hours_ahead <= 24:
        # Středněkrátkodobá (6-24h): ICON nebo ALADIN
        return "ICON"
    elif hours_ahead <= 72:
        # Střednědobá (1-3 dny): ECMWF je nejpřesnější
        return "ECMWF"
    else:
        # Dlouhodobá (3+ dny): MASTER (ensemble) je nejspolehlivější
        return "MASTER"
