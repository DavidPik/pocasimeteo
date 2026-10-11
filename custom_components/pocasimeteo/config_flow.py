"""Config flow for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
from pathlib import Path
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    DB_FILENAME,
    CONF_DATABASE_PATH,
    CONF_STATION,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    CONF_FORECAST_ENTITY_ID,
    CONF_SENSORS,
    CONF_STATISTICS_INTERVAL,
    ALLOWED_STATISTICS_INTERVALS,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_OPTIONS,
    DEFAULT_SENSOR_OPTIONS,
    SENSOR_DEFINITIONS,
    GRAPH_STYLE_SMOOTH,
    GRAPH_STYLE_STEPPED,
)
from .database import PocasimeteoDatabase

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
    
    all_sensor_ids = set(DEFAULT_SENSOR_OPTIONS.keys()) | set(options_sensors.keys())
    if coordinator_sensors:
        all_sensor_ids |= set(coordinator_sensors.keys())

    for sensor_id in sorted(all_sensor_ids):
        if sensor_id in SENSOR_DEFINITIONS:
            meta = SENSOR_DEFINITIONS[sensor_id]
            fallback_order = meta.get("order", 999)
            fallback_color = meta.get("color", "#3b82f6")
            fallback_style = GRAPH_STYLE_STEPPED if sensor_id in ["vitr_rychlost", "vitr_narazy", "vitr_smer", "srazky_intenzita"] else GRAPH_STYLE_SMOOTH
        else:
            fallback_order = 999
            fallback_color = "#3b82f6"
            fallback_style = GRAPH_STYLE_SMOOTH

        current = options_sensors.get(sensor_id, {})

        schema.update({
            vol.Required(f"{sensor_id}_order", default=current.get("order", fallback_order)): int,
            vol.Required(f"{sensor_id}_color", default=current.get("color", fallback_color)): str,
            vol.Required(f"{sensor_id}_style", default=current.get("style", fallback_style)): vol.In([GRAPH_STYLE_SMOOTH, GRAPH_STYLE_STEPPED]),
            vol.Required(f"{sensor_id}_visible", default=current.get("visible", True)): bool,
        })
        
        if sensor_id not in SENSOR_DEFINITIONS:
            schema.update({
                vol.Optional(f"{sensor_id}_delete_config", default=False): bool
            })

    return schema


def convert_user_input_to_options(user_input: dict) -> dict:
    """Převádí vstupy z formuláře a filtruje smazané dynamické senzory."""
    sensors = {}
    
    all_sensor_ids = set()
    for key in user_input.keys():
        if key.endswith("_visible"):
            sensor_id = key[:-8]
            all_sensor_ids.add(sensor_id)

    for sensor_id in all_sensor_ids:
        if user_input.get(f"{sensor_id}_delete_config", False):
            continue
            
        if sensor_id in SENSOR_DEFINITIONS:
            sensor_type = SENSOR_DEFINITIONS[sensor_id].get("sensor_type", "primary")
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
        CONF_STATISTICS_INTERVAL: user_input.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL),
    }

# ------------------------------------------------------------
# OPTIONS FLOW
# ------------------------------------------------------------

class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            new_options = convert_user_input_to_options(user_input)
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(step_id="init", data_schema=self._build_schema())

    def _build_schema(self):
        """Sestaví opravené schéma formuláře s vyloučením vlastní weather entity."""
        registry = er.async_get(self.hass)
        
        station_prefix = (self.config_entry.title or "").lower().replace(" ", "_")
        own_weather_entity = f"weather.{station_prefix}"

        weather_entities = sorted(
            entity.entity_id
            for entity in registry.entities.values()
            if entity.entity_id.startswith("weather.") and entity.entity_id != own_weather_entity
        )

        options = {**DEFAULT_OPTIONS, **self.config_entry.options}
        sensors_config = options.get(CONF_SENSORS, DEFAULT_SENSOR_OPTIONS)

        store = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        coordinator = store.get("coordinator") if isinstance(store, dict) else None
        coordinator_sensors = coordinator.sensors_payload if coordinator else None

        current_forecast = options.get(CONF_FORECAST_ENTITY_ID, "")
        if current_forecast and current_forecast not in weather_entities:
            weather_entities.append(current_forecast)

        schema = {
            vol.Required(CONF_UPDATE_INTERVAL, default=options.get(CONF_UPDATE_INTERVAL, 5)): vol.All(int, vol.Range(min=1, max=60)),
            vol.Optional(CONF_FORECAST_ENTITY_ID, default=current_forecast): vol.In([""] + weather_entities),

            # ⭐ Nová volba – interval statistik
            vol.Required(
                CONF_STATISTICS_INTERVAL,
                default=options.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL),
            ): vol.In(ALLOWED_STATISTICS_INTERVALS),
        }

        schema.update(build_sensor_form(sensors_config, coordinator_sensors))
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
        return PocasimeteoOptionsFlow()
    
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
        errors = {}
        if user_input is not None:
            database_path = user_input.get(CONF_DATABASE_PATH, "").strip()
            try:
                await self._async_prepare_database(database_path)
            except Exception as err:
                _LOGGER.error("Nelze připravit databázi PočasíMeteo: %s", err)
                errors["base"] = "invalid_database"
            else:
                self._database_path = str(Path(database_path).expanduser().resolve())
                options = convert_user_input_to_options(user_input)

                return self.async_create_entry(
                    title=self._station_name,
                    data={
                        CONF_STATION: self._station_name,
                        CONF_API_KEY: self._api_key,
                        CONF_UPDATE_INTERVAL: options[CONF_UPDATE_INTERVAL],
                        CONF_DATABASE_PATH: self._database_path,
                    },
                    options=options,
                )

        return self.async_show_form(
            step_id="config",
            data_schema=self._build_schema(),
            errors=errors,
        )

    def _build_schema(self):
        """Sestaví čisté výchozí schéma s vyloučením budoucí vlastní weather entity."""
        registry = er.async_get(self.hass)
        
        station_prefix = (getattr(self, "_station_name", "") or "").lower().replace(" ", "_")
        own_weather_entity = f"weather.{station_prefix}"

        weather_entities = sorted(
            entity.entity_id
            for entity in registry.entities.values()
            if entity.entity_id.startswith("weather.") and entity.entity_id != own_weather_entity
        )

        default_db_path = str(
            Path(self.hass.config.path(".storage")) / DOMAIN / DB_FILENAME
        )
        schema = {
            vol.Required(CONF_DATABASE_PATH, default=default_db_path): str,
            vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_OPTIONS[CONF_UPDATE_INTERVAL]): vol.All(int, vol.Range(min=1, max=60)),
            vol.Optional(CONF_FORECAST_ENTITY_ID, default=""): vol.In([""] + weather_entities),

            # ⭐ Nová volba – interval statistik
            vol.Required(
                CONF_STATISTICS_INTERVAL,
                default=DEFAULT_STATISTICS_INTERVAL,
            ): vol.In(ALLOWED_STATISTICS_INTERVALS),
        }

        schema.update(build_sensor_form(DEFAULT_SENSOR_OPTIONS))
        return vol.Schema(schema)

    async def _async_prepare_database(self, database_path: str) -> None:
        """Validate an existing database or create and validate a new one."""
        if not database_path:
            raise ValueError("Cesta k databázi nesmí být prázdná.")
        resolved_path = Path(database_path).expanduser()
        if not resolved_path.is_absolute():
            raise ValueError("Cesta k databázi musí být absolutní.")
        database = PocasimeteoDatabase(
            self.hass,
            database_path=resolved_path,
        )
        await database.async_prepare_for_config()

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        """Allow changing the database path from the integration settings."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            database_path = user_input.get(CONF_DATABASE_PATH, "").strip()
            try:
                await self._async_prepare_database(database_path)
            except Exception as err:
                _LOGGER.error("Neplatná databáze PočasíMeteo: %s", err)
                errors["base"] = "invalid_database"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_DATABASE_PATH: str(
                            Path(database_path).expanduser().resolve()
                        )
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_DATABASE_PATH,
                    default=entry.data.get(
                        CONF_DATABASE_PATH,
                        str(Path(self.hass.config.path(".storage")) / DOMAIN / DB_FILENAME),
                    ),
                ): str,
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data) -> FlowResult:
        """Open a database settings dialog after startup validation failed."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        """Validate a replacement database and resume the existing entry."""
        entry = getattr(self, "_reauth_entry", None)
        if entry is None:
            entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
            self._reauth_entry = entry
        errors = {}
        if user_input is not None:
            database_path = user_input.get(CONF_DATABASE_PATH, "").strip()
            try:
                await self._async_prepare_database(database_path)
            except Exception as err:
                _LOGGER.error("Neplatná databáze PočasíMeteo: %s", err)
                errors["base"] = "invalid_database"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_DATABASE_PATH: str(
                            Path(database_path).expanduser().resolve()
                        )
                    },
                )
        default_db_path = entry.data.get(CONF_DATABASE_PATH) or str(
            Path(self.hass.config.path(".storage")) / DOMAIN / DB_FILENAME
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_DATABASE_PATH, default=default_db_path): str,
            }),
            errors=errors,
        )

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
