# PočasíMeteo - Konfigurační Příručka

# Konfigurace integrace PočasíMeteo pro Home Assistant

Integrace umožňuje načítat aktuální meteorologická data z meteostanic služby **PočasíMeteo.cz** a zobrazovat je v Home Assistantu jako senzory a weather entitu.

---

## 🧩 Instalace

### 1. Instalace přes HACS (doporučeno)
1. Otevřete **HACS → Integrations**.
2. Klikněte na **Custom repositories**.
3. Přidejte URL repozitáře: https://github.com/DavidPik/pocasimeteo
4. Vyberte kategorii **Integration**.
5. Nainstalujte integraci **PočasíMeteo**.
6. Restartujte Home Assistant.

### 2. Ruční instalace
Zkopírujte složku: custom_components/pocasimeteo 
              do: /config/custom_components/

a restartujte Home Assistant.

---

## ⚙️ Konfigurace integrace

Integrace se konfiguruje přes UI:

**Nastavení → Zařízení a služby → Přidat integraci → PočasíMeteo**

### Formulář obsahuje:

| Pole | Popis |
|------|-------|
| **Název stanice** | Libovolný název, který se zobrazí v entitách. |
| **API klíč** | API klíč meteostanice z PočasíMeteo.cz. |
| **Interval aktualizace (1–30 min)** | Jak často se mají stahovat data z API. |
| **Externí předpověď (volitelné)** | Výběr existující entity `weather.*` pro forecast. |

---

## 📡 Jak integrace funguje

### 1. Weather entita
Integrace vytvoří entitu: weather.<název_stanice>


Zobrazuje:
- teplotu
- tlak
- vlhkost
- vítr
- nárazy větru
- směr větru
- UV index
- srážky
- sluneční záření

Pokud je vybrána externí předpověď:
- zobrazí se **forecast_daily**
- zobrazí se **forecast_hourly**

### 2. Senzory
Integrace dynamicky vytvoří senzory podle klíčů v API.

Typické senzory:
- `sensor.<stanice>_teplotavnejsi`
- `sensor.<stanice>_vlhkostvnejsi`
- `sensor.<stanice>_tlakrel`
- `sensor.<stanice>_vitr`
- `sensor.<stanice>_vitrnarazy`
- `sensor.<stanice>_vitr_smer`
- `sensor.<stanice>_uvindex`
- `sensor.<stanice>_slunzareni`
- `sensor.<stanice>_srazkyden`
- `sensor.<stanice>_teplotavnitrni`
- `sensor.<stanice>_vlhkostvnitrni`

Jednotky a device_class jsou přiřazeny automaticky.

---

## 🛠️ Odstraňování problémů

### Senzory nemají hodnoty
- Zkontrolujte, zda API klíč vrací data.
- Ověřte, že integrace používá správnou verzi (HACS může kešovat staré ZIPy).
- Restartujte Home Assistant po instalaci nové verze.

### Externí předpověď nefunguje
- Vybraná entita musí být typu `weather.*`.
- Musí obsahovat atributy `forecast`, `forecast_daily` nebo `forecast_hourly`.

### Chyba při instalaci
- Zkontrolujte, zda je složka `custom_components/pocasimeteo` kompletní.
- Ověřte, že používáte Home Assistant 2025.12 nebo novější.

---

## 📄 Licence

Projekt je dostupný pod licencí MIT.

---

## 🤝 Podpora

Pokud narazíte na problém, vytvořte issue v repozitáři:

https://github.com/DavidPik/pocasimeteo/issues



