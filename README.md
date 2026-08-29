# PočasíMeteo – Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/davidpik/pocasimeteo.svg)](https://github.com/davidpik/pocasimeteo/releases)
[![License](https://img.shields.io/github/license/davidpik/pocasimeteo.svg)](LICENSE)

PočasíMeteo je vysoce optimalizovaná integrace pro Home Assistant (testováno na HA Green), která zajišťuje:
- živé meteorologické hodnoty z osobních meteostanic registrovaných na portálu PočasíMeteo.cz,
- automatické doplňování chybějící historie do Recorderu,
- přesné 24h statistiky pro frontendovou kartu pocasimeteo-card,
- dynamické objevování nových čidel,
- pokročilou konfiguraci vzhledu grafů přes OptionsFlow.

Integrace je navržena jako plně samostatný backend, který poskytuje kompletní meteorologická metadata pro frontendovou kartu `pocasimeteo-card.js` a tvoří s ní ucelený meteorologický dashboard.

---

## Hlavní funkce

### ✔️ Stabilní asynchronní architektura
Veškeré zpracování probíhá v dedikovaném koordinátoru:
- API dotazy jsou řízené přes DataUpdateCoordinator.
- Výpočty probíhají v izolovaném vlákně, aby nezatěžovaly hlavní smyčku HA.
- Statistické výpočty (min/max, kruhové statistiky větru) probíhají v jediném průchodu datasetu.

### ✔️ Automatické doplňování historie (Recorder)
Integrace obsahuje vlastní background worker, který:
- analyzuje 5minutovou historii z API,
- porovnává ji s databází Recorderu,
- dopisuje pouze chybějící body,
- používá moderní schéma HA (States, StatesMeta, StateAttributes),
- provádí zápisy přes Recorder executor, aby byla zajištěna bezpečnost a rychlost.

Díky tomu je databáze vždy konzistentní a grafy na kartě mají spojitou časovou osu.

### ✔️ Klouzavé 24hodinové statistiky v RAM
Koordinátor neustále udržuje přesné 24h klouzavé okno dat, ze kterého počítá:
- **Minima a maxima** pro všechny číselné senzory.
- **Kruhové statistiky větru** pro senzor směru větru (Vektorový průměrný směr, Převládající modus a Úhlový rozptyl/Variance). Tyto atributy jsou okamžitě dostupné pro vykreslení větrné růžice na frontendové kartě.

### ✔️ Pokročilá správa vzhledu senzorů (Options Flow)
Přímo v uživatelském nastavení integrace (tlačítko **Nastavit**) lze spravovat vzhled pro **Lovelace kartu**:
- Pořadí zobrazení grafů (`order`)
- Barva čáry grafu v HEX formátu (`color`)
- Styl grafu – plynulý/čárový vs. schodovitý (`smooth` / `stepped`)
- Viditelnost čidla (`visible`)

Dále lze v nastavení integrace řídit četnost aktualizace dat a časový interval, za který budou připravená data pro frontendovou kartu.

### ✔️ Plná podpora pro dynamická čidla
Pokud k meteostanici připojíte dodatečné senzory (např. čidlo v bazénu, půdní vlhkoměr), integrace je za běhu detekuje z API a vytvoří pro ně entity v HA. Tyto entity lze následně plně konfigurovat, měnit jejich styl, barvu nebo jejich konfigurační záznam z paměti trvale smazat, pokud čidlo odpojíte.

---

## Instalace

### 1. Přes HACS (Doporučeno)
1. V levém menu otevřete **HACS → Integrace**
2. V pravém horním rohu klikněte na tři tečky a zvolte **Uživatelské repozitáře** (Custom repositories)
3. Vložte URL adresu: `https://github.com/DavidPik/pocasimeteo`
4. Jako Kategorii zvolte **Integrace** a klikněte na **Přidat**
5. Vyhledejte integraci **PočasíMeteo** v HACS katalogu a stáhněte ji.
6. **Restartujte Home Assistant.**

### 2. Manuální instalace
1. Stáhněte si zdrojové kódy z tohoto repozitáře.
2. Zkopírujte složku `custom_components/pocasimeteo` do vašeho adresáře `config/custom_components/` v Home Assistantovi.
3. **Restartujte Home Assistant.**

---

## Prvotní nastavení

1. Přejděte do **Nastavení → Zařízení a služby**
2. Vpravo dole klikněte na **Přidat integraci**
3. Vyhledejte **PočasíMeteo**
4. Zadejte zvolený **Název stanice** (např. *GARNI1*) a váš unikátní **API klíč** z portálu Pocasimeteo.cz.
5. V dalším kroku průvodce se vám zobrazí tovární konfigurace senzorů, kterou potvrďte.

Integrace okamžitě vytvoří weather entitu a všechny dostupné senzory.
---

## Struktura entit a ID

Integrace přísně dbá na čistou jmennou sémantiku. ID entit se generují na základě systémového slugu názvu vaší stanice (převedeno na malá písmena, mezery nahrazeny podtržítkem).

### Hlavní weather entita
Vytvoří se jedna hlavní entita:
- `weather.<název_stanice>` (např. `weather.gar632`)

Tato entita publikuje standardizované HA stavy (teplota, tlak, vlhkost, rychlost a směr větru) a minimální sadu extra atributů schválených pro frontendovou kartu (lokalita stanice, URL webkamery, srážky za den a dynamické pole `sensors` s metadaty pro bleskové načtení grafů).

### Senzorové entity
Všechny senzory jsou dostupné jako samostatné entity ve formátu:
- `sensor.<název_stanice>_<sensor_id>`

Příklady vygenerovaných entit:
- `sensor.gar632_teplota_vnejsi` – Venkovní teplota
- `sensor.gar632_vlhkost_vnejsi` – Venkovní vlhkost
- `sensor.gar632_tlak_relativni` – Relativní tlak vzduchu
- `sensor.gar632_intenzita_srazek` – Intenzita srážek (mm/h)
- `sensor.gar632_vitr_smer` – Směr větru (obsahuje 24h rolling atributy průměru, modu a rozptylu)

---

## Konfigurace za běhu (Options Flow)

Kdykoliv kliknete v rozhraní Home Assistenta na tlačítko **Nastavit** u karty PočasíMeteo, otevře se pokročilý konfigurační formulář, kde můžete měnit:
- **Interval aktualizace:** (1 až 60 minut)
- **Entita předpovědi:** Možnost propojit integraci s libovolnou jinou weather entitou v domě (např. Met.no, CHMI), ze které bude frontendová karta číst budoucí dny. Systém z bezpečnostních důvodů automaticky filtruje a skrývá sebe sama, aby nedošlo k zacyklení předpovědi.
- **Vzhled jednotlivých čidel:** Pro každé čidlo (statické i dynamicky objevené) můžete určit řazení, barvu a styl.
- **Smazání konfigurace:** U dynamických čidel se zobrazí zaškrtávací políčko pro trvalé odstranění jejich konfiguračních dat z paměti integrace.

---

## Spolupráce s Lovelace kartou

Tato integrace byla navržena pro maximální synergii s kartou **`pocasimeteo-card`**. Karta se díky předávanému poli metadat v weather entitě dokáže bleskově vykreslit bez čekání a paralelně si na pozadí dotáhne 24hodinovou historii z Recorderu pro každé čidlo zvlášť.

---

## Řešení problémů

### Senzory jsou ve stavu `unavailable`
- Zkontrolujte funkčnost API klíče.
- Pokud jste integraci aktualizovali ze starší vývojové verze, Home Assistant může v registru držet stará ID entit (např. s koncovkou `_venkovni` namísto nového `_vnejsi`). Přejděte v HA do *Nastavení -> Zařízení a služby -> PočasíMeteo*, rozklikněte daný senzor a manuálně v jeho nastavení upravte ID entity na správný tvar (např. `sensor.gar632_teplota_vnejsi`).

### Grafy na kartě jsou prázdné
- Ujistěte se, že máte v Home Assistantovi zapnutou komponentu `recorder` (ukládání historie).
- Po prvním přidání integrace trvá několik minut, než databáze nasbírá dostatek bodů pro vykreslení spojité čáry grafu.

---

## Licence
Tento projekt je publikován pod licencí MIT.

## Kredity
- **Vývojář:** David Pikálek
- **Poskytovatel meteorologických dat:** [PočasíMeteo.cz](https://pocasimeteo.cz)
