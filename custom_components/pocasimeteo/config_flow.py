"""Config flow for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    CONF_STATION,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    CONF_FORECAST_ENTITY_ID,
    CONF_SENSORS,
    DEFAULT_OPTIONS,
    DEFAULT_SENSOR_OPTIONS,
    SENSOR_DEFINITIONS,
    GRAPH_STYLE_SMOOTH,
    GRAPH_STYLE_STEPPED,
)

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# SHARED FORM LOGIC
# ------------------------------------------------------------

def build_sensor_form(options_sensors: dict, coordinator_sensors: dict = None) -> dict:
    """
    Sestaví formulář pro konfiguraci vzhledu.
    Defaultní senzory drží typ pevně, u dynamických je typ fixován na secondary/dynamic.
    """
    schema = {}
    
    # Spojíme známé statické senzory s těmi, které jsou reálně uloženy nebo nalezeny v koordinátoru
    all_sensor_ids = set(DEFAULT_SENSOR_OPTIONS.keys()) | set(options_sensors.keys())
    if coordinator_sensors:
        all_sensor_ids |= set(coordinator_sensors.keys())

    for sensor_id in sorted(all_sensor_ids):
        # 1. KROK: Zjistíme výchozí hodnoty (tovární nastavení) podle toho, o jaký typ čidla jde
        if sensor_id in SENSOR_DEFINITIONS:
            meta = SENSOR_DEFINITIONS[sensor_id]
            fallback_order = meta.get("order", 999)
            fallback_color = meta.get("color", "#3b82f6")
            fallback_style = GRAPH_STYLE_STEPPED if sensor_id in ["vitr_rychlost", "vitr_narazy", "vitr_smer", "intenzita_srazek"] else GRAPH_STYLE_SMOOTH
        else:
            fallback_order = 999
            fallback_color = "#3b82f6"
            fallback_style = GRAPH_STYLE_SMOOTH

        current = options_sensors.get(sensor_id, {})

        # 2. KROK: Do formuláře přidáme standardní políčka (řazení, barva, styl grafu, viditelnost)
        schema.update({
            vol.Required(f"{sensor_id}_order", default=current.get("order", fallback_order)): int,
            vol.Required(f"{sensor_id}_color", default=current.get("color", fallback_color)): str,
            vol.Required(f"{sensor_id}_style", default=current.get("style", fallback_style)): vol.In([GRAPH_STYLE_SMOOTH, GRAPH_STYLE_STEPPED]),
            vol.Required(f"{sensor_id}_visible", default=current.get("visible", True)): bool,
        })
        
        # 3. KROK: Pouze pokud jde o dynamické čidlo (není v const.py), dovolíme uživateli ho smazat
        if sensor_id not in SENSOR_DEFINITIONS:
            schema.update({
                vol.Optional(f"{sensor_id}_delete_config", default=False): bool
            })

    return schema


def convert_user_input_to_options(user_input: dict) -> dict:
    """Převádí vstupy z formuláře a filtruje smazané dynamické senzory."""
    sensors = {}
    
    # Extrahujeme unikátní sensor_id ze zadaných polí formuláře
    all_sensor_ids = set()
    for key in user_input.keys():
        if key.endswith("_visible"):
            sensor_id = key[:-8]
            all_sensor_ids.add(sensor_id)

    for sensor_id in all_sensor_ids:
        # Pokud uživatel zaškrtl smazání konfigurace dynamického čidla, zahodíme ho
        if user_input.get(f"{sensor_id}_delete_config", False):
            continue
            
        # Zjistíme typ: výchozí senzory ho mají z const.py, dynamické jsou vždy secondary
        if sensor_id in SENSOR_DEFINITIONS:
            sensor_type = SENSOR_DEFINITIONS[sensor_id].get("type", "primary")
        else:
            sensor_type = "secondary"

        sensors[sensor_id] = {
            "type": sensor_type,
            "order": user_input[f"{sensor_id}_order"],
            "color": user_input[f"{sensor_id}_color"],
            "style": user_input[f"{sensor_id}_style"],
            "visible": user_input[f"{sensor_id}_visible"],
        }

    return {
        CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL],
        CONF_FORECAST_ENTITY_ID: user_input.get(CONF_FORECAST_ENTITY_ID, ""),
        CONF_SENSORS: sensors,
    }


# ------------------------------------------------------------
# OPTIONS FLOW
# ------------------------------------------------------------

class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates za běhu integrace."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            new_options = convert_user_input_to_options(user_input)
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="init", data_schema=self._build_schema())

    def _build_schema(self):
        registry = er.async_get(self.hass)
        weather_entities = sorted(
            entity.entity_id
            for entity in registry.entities.values()
            if entity.entity_id.startswith("weather.")
        )

        options = {**DEFAULT_OPTIONS, **self.config_entry.options}
        sensors = options.get(CONF_SENSORS, DEFAULT_SENSOR_OPTIONS)

        # ARCHITEKTURA: Vytáhneme si aktuální běžící data z koordinátoru pro zobrazení dynamických čidel
        store = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        coordinator = store.get("coordinator")
        coordinator_sensors = coordinator.sensors_payload if coordinator else None

        schema = {
            vol.Required(CONF_UPDATE_INTERVAL, default=options[CONF_UPDATE_INTERVAL]): vol.All(int, vol.Range(min=1, max=60)),
            vol.Optional(CONF_FORECAST_ENTITY_ID, default=options[CONF_FORECAST_ENTITY_ID]): vol.In([""] + weather_entities),
        }

        schema.update(build_sensor_form(sensors, coordinator_sensors))
        return vol.Schema(schema)


# ------------------------------------------------------------
# CONFIG FLOW (initial setup)
# ------------------------------------------------------------

class PocasimeteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial configuration při přidání integrace."""
    VERSION = 4

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> PocasimeteoOptionsFlow:
        """ARCHITEKTURA HA: Oficiální registrace toku možností nastavení (Options Flow)."""
        return PocasimeteoOptionsFlow(config_entry)
    
    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            station_name = user_input[CONF_STATION]
            api_key = user_input[CONF_API_KEY].strip()

            if not await self._async_validate_api_key(self.hass, api_key):
                errors["base"] = "invalid_api_key"
            else:
                await self.async_set_unique_id(api_key)
                self._abort_if_unique_id_configured()

                self._station_name = station_name
                self._api_key = api_key
                return await self.async_step_config()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_STATION): str,
                vol.Required(CONF_API_KEY): str,
            }),
            errors=errors,
        )

    async def async_step_config(self, user_input=None) -> FlowResult:
        if user_input is not None:
            options = convert_user_input_to_options(user_input)

            return self.async_create_entry(
                title=self._station_name,
                data={
                    CONF_STATION: self._station_name,
                    CONF_API_KEY: self._api_key,
                    CONF_UPDATE_INTERVAL: options[CONF_UPDATE_INTERVAL],
                },
                options=options,
            )

        return self.async_show_form(step_id="config", data_schema=self._build_schema())

    def _build_schema(self):
        registry = er.async_get(self.hass)
        weather_entities = sorted(
            entity.entity_id
            for entity in registry.entities.values()
            if entity.entity_id.startswith("weather.")
        )

        schema = {
            vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_OPTIONS[CONF_UPDATE_INTERVAL]): vol.All(int, vol.Range(min=1, max=60)),
            vol.Optional(CONF_FORECAST_ENTITY_ID, default=""): vol.In([""] + weather_entities),
        }

        schema.update(build_sensor_form(DEFAULT_SENSOR_OPTIONS))
        return vol.Schema(schema)

    async def _async_validate_api_key(self, hass: HomeAssistant, api_key: str) -> bool:
        from .const import API_URL_BASE

        url = f"{API_URL_BASE}?KlicApi={api_key}"

        try:
            session = aiohttp_client.async_get_clientsession(hass)
            async with asyncio.timeout(10):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    return isinstance(data, (dict, list))
        except Exception:
            return False
