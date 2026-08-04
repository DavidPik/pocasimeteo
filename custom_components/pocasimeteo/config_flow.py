"""Config flow for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio  # ODCHYLKA: Používáme standardní asyncio namísto deprecovaného async_timeout
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
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
    GRAPH_STYLE_SMOOTH,
    GRAPH_STYLE_STEPPED,
)

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# SHARED FORM LOGIC
# ------------------------------------------------------------

def build_sensor_form(options_sensors: dict) -> dict:
    """Sestaví dynamický formulář pro konfiguraci vzhledu prvků karty."""
    schema = {}

    for sensor_id, meta in DEFAULT_SENSOR_OPTIONS.items():
        current = options_sensors.get(sensor_id, meta)

        # ARCHITEKTURA FRONTENDU: Umožňujeme uživateli přímo v integraci definovat pořadí,
        # barvu a styl vykreslení grafu (plynulý vs. schodovitý). Tato metadata si frontendová
        # karta přečte z extra_state_attributes jednotlivých senzorů.
        schema.update({
            vol.Required(f"{sensor_id}_order", default=current["order"]): int,
            vol.Required(f"{sensor_id}_color", default=current["color"]): str,
            vol.Required(f"{sensor_id}_style", default=current["style"]): vol.In([GRAPH_STYLE_SMOOTH, GRAPH_STYLE_STEPPED]),
            vol.Required(f"{sensor_id}_visible", default=current["visible"]): bool,
        })

    return schema


def convert_user_input_to_options(user_input: dict) -> dict:
    """Zpracuje vstupy z formuláře do struktury uložené v konfiguraci."""
    sensors = {
        sensor_id: {
            "order": user_input[f"{sensor_id}_order"],
            "color": user_input[f"{sensor_id}_color"],
            "style": user_input[f"{sensor_id}_style"],
            "visible": user_input[f"{sensor_id}_visible"],
        }
        for sensor_id in DEFAULT_SENSOR_OPTIONS
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
    """Správa nastavení integrace za běhu (Nastavení -> Integrace -> Nastavit)."""

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

        schema = {
            vol.Required(CONF_UPDATE_INTERVAL, default=options[CONF_UPDATE_INTERVAL]): vol.All(int, vol.Range(min=1, max=60)),
            vol.Optional(CONF_FORECAST_ENTITY_ID, default=options[CONF_FORECAST_ENTITY_ID]): vol.In([""] + weather_entities),
        }

        schema.update(build_sensor_form(sensors))
        return vol.Schema(schema)


# ------------------------------------------------------------
# CONFIG FLOW (initial setup)
# ------------------------------------------------------------

class PocasimeteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Prvotní nastavení integrace při přidávání do Home Assistenta."""
    VERSION = 4

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
            async with asyncio.timeout(10):  # ODCHYLKA: Moderní asynchronní timeout
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    return isinstance(data, (dict, list))
        except Exception:
            return False
