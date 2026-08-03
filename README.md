# PočasíMeteo – Home Assistent Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/davidpik/pocasimeteo.svg)](https://github.com/davidpik/pocasimeteo/releases)
[![License](https://img.shields.io/github/license/davidpik/pocasimeteo.svg)](LICENSE)

# PočasíMeteo – Home Assistant Integration

PočasíMeteo is a custom integration for Home Assistant that provides real‑time and historical weather data from **PočasíMeteo.cz** personal weather stations.  
This is the **first public release** of the integration.

The integration offers:

- Live weather measurements (temperature, humidity, pressure, wind, UV, solar radiation, rainfall)
- Automatic import of 5‑minute historical data into Home Assistant Recorder
- Rolling 24‑hour statistics (min/max, wind direction avg/mode/variance)
- Full sensor metadata (graph color, graph style, order, visibility)
- Weather entity with station metadata and webcam URL
- Dynamic discovery of additional sensors provided by the API

---

## Features

### ✔ Real‑time weather data
All primary and secondary sensors from PočasíMeteo API are exposed as Home Assistant sensor entities.

### ✔ 5‑minute history import
The integration automatically imports historical measurements into Home Assistant Recorder, allowing graphs to show complete history even after restarts.

### ✔ Rolling 24‑hour statistics
The coordinator computes:
- min / max for numeric sensors  
- circular statistics for wind direction:
  - average  
  - mode  
  - variance  

### ✔ Weather entity
A dedicated `weather` entity provides:
- temperature  
- pressure  
- humidity  
- wind speed & gust  
- wind bearing  
- rainfall intensity  
- station metadata  
- webcam URL  

### ✔ Sensor customization
Users can configure:
- sensor order  
- graph color  
- graph style (smooth / stepped)  
- sensor visibility  
- update interval  
- forecast entity  

All configuration is available through the Home Assistant UI.

---

## Installation

### HACS (recommended)
1. Open **HACS → Integrations**
2. Click **Custom repositories**
3. Add repository: https://github.com/DavidPik/pocasimeteo
   Type: **Integration**
4. Search for **PočasíMeteo** in HACS and install it.
5. Restart Home Assistant.

### Manual installation
1. Download the repository.
2. Copy the folder: custom_components/pocasimeteo
   into: config/custom_components/pocasimeteo

3. Restart Home Assistant.

---

## Configuration

### Step 1 — Add the integration
1. Go to **Settings → Devices & Services**
2. Click **Add Integration**
3. Search for **PočasíMeteo**
4. Enter:
   - Station name  
   - API key  

The API key is provided by PočasíMeteo.cz.

### Step 2 — Configure sensors
After adding the integration, you can configure:

- Update interval  
- Forecast entity  
- Sensor order  
- Sensor graph color  
- Sensor graph style  
- Sensor visibility  

All settings are available via **Configure** on the integration card.

---

## Entities

### Weather entity

weather.<station_name>

Attributes include:
- station location  
- webcam URL  
- update interval  
- timestamp  
- rainfall total  
- sorted list of sensors with metadata  

### Sensor entities
Each sensor is exposed as:
sensor.<station_name>_<sensor_id>

Examples:
- `sensor.pocasimeteo_teplota_vnejsi`
- `sensor.pocasimeteo_vitr_rychlost`
- `sensor.pocasimeteo_uv_index`
- `sensor.pocasimeteo_srazkyden`

Dynamic sensors are automatically detected and created.

---

## Options

The integration stores configuration in `config_entry.options`:

```json
{
  "update_interval": 5,
  "forecast_entity_id": "",
  "sensors": {
    "teplota_vnejsi": {
      "order": 1,
      "color": "#f59e0b",
      "style": "smooth",
      "visible": true
    },
    "vitr_rychlost": {
      "order": 5,
      "color": "#3b82f6",
      "style": "stepped",
      "visible": true
    }
  }
}
```

## Troubleshooting
### No data appears
- Verify your API key is correct.
- Check PočasíMeteo API availability.
- Restart Home Assistant.

### Sensors missing
- Some sensors appear only when the station reports them.
- Dynamic sensors are created automatically when detected.

### History not visible
- Recorder must be enabled.
- The integration imports 5‑minute history automatically.

## Known limitations
- Forecast entity is optional and depends on other weather integrations.
- Some stations may not provide all sensor types.
- Rainfall intensity is computed from history and may differ slightly from API values.

## License
MIT License

## Credits
Developed by David Pikálek  
Weather data provided by PočasíMeteo.cz
