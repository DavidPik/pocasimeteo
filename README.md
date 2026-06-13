# PočasíMeteo – integrace meteostanice pro Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/davidpik/pocasimeteo.svg)](https://github.com/davidpik/pocasimeteo/releases)
[![License](https://img.shields.io/github/license/davidpik/pocasimeteo.svg)](LICENSE)

Integrace umožňuje připojit **meteostanici PočasíMeteo.cz** do Home Assistantu.  
Získává **aktuální měření** přímo z API meteostanice a poskytuje:

- entitu **weather** (aktuální počasí + volitelná předpověď z jiné integrace)
- **dynamicky generované senzory** pro všechna dostupná měření
- podporu doplňkových čidel (Te1–Te5, Vl1–Vl5, CO₂, PM1/PM2.5…)
- podporu volitelného forecastu z jiné weather integrace

Integrace je plně kompatibilní s HACS.

---

## 📦 Instalace

### 🔧 Instalace přes HACS (doporučeno)

1. Otevřete **HACS → Integrations**
2. Klikněte na **Custom repositories**
3. Přidejte URL repozitáře: https://github.com/DavidPik/pocasimeteo (github.com in Bing)
4. Typ: **Integration**
5. Vyhledejte **PočasíMeteo**
6. Klikněte **Install**

### 🗂️ Manuální instalace

Zkopírujte složku: custom_components/pocasimeteo
Restartujte Home Assistant.

---

## ⚙️ Konfigurace

Integrace se konfiguruje přes:

**Nastavení → Integrace → Přidat integraci → PočasíMeteo**

Budete vyzváni k zadání:

| Pole | Popis |
|------|--------|
| **Název stanice** | Libovolný název, který se zobrazí v HA |
| **API klíč** | Hodnota `KlicApi` z PočasíMeteo |
| **Interval aktualizace** | 1–30 minut |
| **Weather entita pro předpověď (volitelné)** | Např. `weather.openweathermap` |

### 🔍 Kde získat API klíč?

API klíč najdete v URL meteostanice: https://ext.pocasimeteo.cz/ms/?KlicApi=XXXXXXXX (ext.pocasimeteo.cz in Bing)

---

## 🌦️ Entita Weather

Integrace poskytuje entitu: weather.<název_stanice>

### Obsahuje:

- aktuální teplotu  
- vlhkost  
- tlak  
- rychlost větru  
- směr větru  
- nárazy větru  
- UV index  
- sluneční záření  
- srážky  

### Předpověď

Pokud v konfiguraci vyberete jinou weather entitu (např. OpenWeatherMap), integrace:

- použije **vlastní aktuální data**
- a **forecast převezme z vybrané entity**

---

## 📡 Senzory

Integrace automaticky vytvoří senzory podle toho, co meteostanice vrací.

### Typické senzory:

| Název | Klíč API | Jednotka |
|-------|----------|----------|
| Vnější teplota | `TeplotaVnejsi` | °C |
| Vnější vlhkost | `VlhkostVnejsi` | % |
| Tlak vzduchu | `TlakRel` | hPa |
| Rychlost větru | `Vitr` | m/s |
| Nárazy větru | `VitrNarazy` | m/s |
| Směr větru | `VitrSmer` | ° |
| Denní srážky | `SrazkyDen` | mm |
| Intenzita srážek | `rainIntensity` | mm/5min |
| Sluneční záření | `SlunZareni` | W/m² |
| UV index | `UVindex` | — |
| Vnitřní teplota | `TeplotaVnitrni` | °C |
| Vnitřní vlhkost | `VlhkostVnitrni` | % |

### Doplňková čidla (pokud existují):

- `Te1`–`Te5` (teploty)
- `Vl1`–`Vl5` (vlhkosti)
- `Co2`
- `Pm1`, `Pm2`
- `VlP`, `VlP2`
- další hodnoty podle stanice

Senzory se generují **automaticky**, není nutná žádná konfigurace.

---

## 🧪 Troubleshooting

### ❗ „Invalid API key“
- API klíč je chybný nebo stanice není dostupná  
- ověřte URL meteostanice

### ❗ Senzory se nezobrazují
- zkontrolujte, zda API vrací hodnoty  
- otevřete:  
  `https://ext.pocasimeteo.cz/ms/api/weather?KlicApi=XXXXXX`

### ❗ Forecast nefunguje
- vybraná weather entita musí mít atribut `forecast`  
- ověřte v Developer Tools → States

---

## 🧱 Struktura projektu

custom_components/pocasimeteo/
│
├── init.py
├── const.py
├── coordinator.py
├── config_flow.py
├── weather.py
└── sensor.py

---

## 📄 Licence

MIT License  
© 2024–2026 David Pikál

---




