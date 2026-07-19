"""Config flow for PočasíMeteo integration."""

from __future__ import annotations

import logging
import aiohttp
import async_timeout
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
    API_URL_TEMPLATE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DEFAULT_SENSORS_OPTIONS,
    DEFAULT_ALL_SENSOR_IDS,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial configuration flow for PočasíMeteo."""

    VERSION = 1

    # ----------------------------------------------------------------------
    # Step: User input
    # ----------------------------------------------------------------------
    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            station_name = user_input[CONF_STATION]
            api_key = user_input[CONF_API_KEY]
            interval = user_input[CONF_UPDATE_INTERVAL]
            forecast_entity = user_input.get("forecast_entity_id")

            # Validate update interval
            try:
                interval = int(interval)
                if interval < 1 or interval > 30:
                    errors["base"] = "invalid_interval"
            except Exception:
                errors["base"] = "invalid_interval"

            # Validate API key by calling PočasíMeteo API
            if not errors:
                if not await self._async_validate_api_key(self.hass, api_key):
                    errors["base"] = "invalid_api_key"

            # Validate forecast entity
            if not errors and forecast_entity:
                if not self._is_valid_forecast_entity(self.hass, forecast_entity):
                    errors["base"] = "invalid_forecast_entity"

            # Create entry
            if not errors:
                await self.async_set_unique_id(api_key)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=station_name,
                    data={
                        CONF_STATION: station_name,
                        CONF_API_KEY: api_key,
                        CONF_UPDATE_INTERVAL: interval,
                    },
                    options={
                        "update_interval": interval,
                        "forecast_entity_id": forecast_entity or "",
                        # Only one canonical structure: list of sensor objects
                        "sensors": DEFAULT_SENSORS_OPTIONS.copy(),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=await self._get_schema(),
            errors=errors,
        )

    # ----------------------------------------------------------------------
    # Schema for initial form
    # ----------------------------------------------------------------------
    async def _get_schema(self) -> vol.Schema:
        registry = er.async_get(self.hass)
        weather_entities = sorted(
            [
                entity.entity_id
                for entity in registry.entities.values()
                if entity.entity_id.startswith("weather.")
            ]
        )

        return vol.Schema(
            {
                vol.Required(CONF_STATION): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=DEFAULT_UPDATE_INTERVAL_MINUTES,
                ): vol.All(int, vol.Range(min=1, max=30)),
                vol.Optional("forecast_entity_id", default=None): vol.In(
                    [None] + weather_entities
                ),
            }
        )

    # ----------------------------------------------------------------------
    # API key validation
    # ----------------------------------------------------------------------
    async def _async_validate_api_key(self, hass: HomeAssistant, api_key: str) -> bool:
        """Validate API key by calling PočasíMeteo API."""
        url = API_URL_TEMPLATE.format(api_key=api_key)

        try:
            session = aiohttp_client.async_get_clientsession(hass)
            async with async_timeout.timeout(10):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("API returned HTTP %s", resp.status)
                        return False
                    data = await resp.json()
        except Exception as err:
            _LOGGER.error("API validation error: %s", err)
            return False

        # API returns either list or dict
        if isinstance(data, list) and data:
            return True
        if isinstance(data, dict) and ("data" in data or "Zprava" in data):
            return True

        return False

    # ----------------------------------------------------------------------
    # Forecast entity validation
    # ----------------------------------------------------------------------
    def _is_valid_forecast_entity(self, hass: HomeAssistant, entity_id: str) -> bool:
        """Check if selected entity exists and is a weather entity."""
        if not entity_id:
            return True

        state = hass.states.get(entity_id)
        if not state:
            return False

        return state.domain == "weather"

    # ----------------------------------------------------------------------
    # Options flow
    # ----------------------------------------------------------------------
    def async_get_options_flow(self, config_entry):
        return PocasimeteoOptionsFlow(config_entry)


# ======================================================================
# Options Flow
# ======================================================================

class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options for PočasíMeteo."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry

    # ------------------------------------------------------------------
    # Step 1: Select sensors
    # ------------------------------------------------------------------
    async def async_step_init(self, user_input=None):
        sensors = self.config_entry.options.get("sensors", DEFAULT_SENSORS_OPTIONS.copy())
        existing_ids = [s["id"] for s in sensors]

        schema = vol.Schema(
            {
                vol.Optional("sensor_list", default=existing_ids): vol.All([str]),
                vol.Optional("add_custom_sensor", default=""): str,
            }
        )

        if user_input is not None:
            sensor_list = user_input.get("sensor_list", [])
            custom = user_input.get("add_custom_sensor", "").strip()

            if custom:
                sensor_list.append(custom)

            # Remove duplicates
            sensor_list = list(dict.fromkeys(sensor_list))

            self._sensor_ids = sensor_list
            return await self.async_step_types()

        return self.async_show_form(step_id="init", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 2: Assign types
    # ------------------------------------------------------------------
    async def async_step_types(self, user_input=None):
        sensor_ids = getattr(self, "_sensor_ids", [])

        schema_dict = {}
        for sid in sensor_ids:
            # Default type based on definitions
            default_type = (
                "primary"
                if sid in DEFAULT_ALL_SENSOR_IDS
                and sid in [s["id"] for s in DEFAULT_SENSORS_OPTIONS if s["type"] == "primary"]
                else "secondary"
            )
            schema_dict[vol.Required(f"type_{sid}", default=default_type)] = vol.In(
                ["primary", "secondary"]
            )

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            self._sensor_types = {
                sid: user_input[f"type_{sid}"] for sid in sensor_ids
            }
            return await self.async_step_order()

        return self.async_show_form(step_id="types", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 3: Assign order
    # ------------------------------------------------------------------
    async def async_step_order(self, user_input=None):
        sensor_ids = getattr(self, "_sensor_ids", [])

        schema_dict = {}
        for sid in sensor_ids:
            schema_dict[vol.Required(f"order_{sid}", default=1)] = vol.All(
                int, vol.Range(min=1, max=999)
            )

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            sensors_final = []
            for sid in sensor_ids:
                sensors_final.append(
                    {
                        "id": sid,
                        "type": self._sensor_types[sid],
                        "order": user_input[f"order_{sid}"],
                    }
                )

            return self.async_create_entry(
                title="Senzory",
                data={"sensors": sensors_final},
            )

        return self.async_show_form(step_id="order", data_schema=schema)
