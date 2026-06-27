# Changelog – PočasíMeteo Home Assistant Integration
## Verze 6.2.1 - Doplněn senzor VitrSmer

V této verzi jsou doplněné funkce pro vytváření senzoru pro Směr větru a jeho statistické atributy

### 🔧 Hlavní změny a opravy

- **Doplněné definice sensoru** - nový senzor pro směr větru
  - název "VitrSmer"
  - jednotky "°"
  - doplňkové statistické atributy "_modus", "_avg", "_variability"
- **Doplnění definice weather entity** - doplněný atribut "wind_bearing" pro směr větru

## Verze 6.0.7 – Kompletní modernizace integrace

Tato verze představuje zásadní přepracování původního projektu **glaverCZ/pocasimeteo** s cílem zajistit plnou kompatibilitu s Home Assistant 2026.x, stabilní provoz a rozšířenou funkcionalitu.

### 🔧 Hlavní změny a opravy

- **Kompletní refaktorace kódu** celé integrace do moderní struktury Home Assistantu (config_flow, coordinator, platformy).
- **Oprava API klienta** – nahrazení zakázaného `aiohttp.ClientSession()` za `aiohttp_client.async_get_clientsession()` (nutné pro HA 2025+).
- **Oprava importů a kompatibility** s Home Assistant 2026:
  - odstranění `SensorDeviceClass.NONE`
  - odstranění `SensorDeviceClass.UV_INDEX`
  - přechod na `UnitOfTemperature.CELSIUS`
  - aktualizace entity registry API
- **Oprava chybějící konstanty** `CONF_UPDATE_INTERVAL` v `const.py`.
- **Oprava chybných importů** v `config_flow.py` a `coordinator.py`.
- **Kompletní přepsání platformy `sensor`**:
  - dynamická detekce všech měřených hodnot z API
  - automatické vytváření senzorů podle klíčů v JSON
  - správné přiřazení jednotek a device_class
- **Vylepšená platforma `weather`**:
  - zobrazení aktuálních hodnot z meteostanice
  - možnost volitelného připojení externí předpovědi (forecast)
- **Oprava chyb při načítání integrace** (Invalid handler, ImportError, AttributeError).
- **Odstranění duplicitních a neplatných souborů** (původní chybný `sensor.py`).
- **Vyčištění projektu**:
  - odstranění starých částí kódu
  - sjednocení názvů entit
  - doplnění komentářů a dokumentace

### ✨ Nové funkce

- **Volitelná integrace externí předpovědi** – možnost vybrat libovolnou entitu `weather.*` jako zdroj forecastu.
- **Dynamické senzory** – integrace automaticky vytvoří senzory podle skutečných dat z meteostanice.
- **Podpora metadat stanice** (název lokality, doplňková čidla, webkamera).
- **Plná kompatibilita s HACS** včetně `hacs.json`.

### 🧹 Další vylepšení

- Zlepšené logování a diagnostika.
- Stabilní první načtení dat (opraveno selhávání `async_config_entry_first_refresh()`).
- Vyšší odolnost vůči změnám API.
- Vyčištěná struktura projektu a příprava pro další rozšíření.

---

Tato verze je plně funkční, stabilní a připravená pro dlouhodobé používání v Home Assistantu.
