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
)

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# Default sensors (can be extended by user)
# ------------------------------------------------------------

DEFAULT_SENSOR_LIST = [
    "TeplotaVnejsi",
    "VlhkostVnejsi",
    "TlakRel",
    "Vitr",
    "VitrSmer",
    "UVindex",
    "Srazky_intensity",
    "TeplotaVnitrni",
    "VlhkostVnitrni",
]


class PocasimeteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PočasíMeteo."""

    VERSION = 2

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            station_name = user_input.get(CONF_STATION)
            api_key = user_input.get(CONF_API_KEY)
            interval = user_input.get(CONF_UPDATE_INTERVAL)
            forecast_entity = user_input.get("forecast_entity_id")

            # Validate interval
            try:
                interval = int(interval)
                if interval < 1 or interval > 30:
                    errors["base"] = "invalid_interval"
            except Exception:
                errors["base"] = "invalid_interval"

            # Validate API key
            if not errors:
                is_valid = await self._async_validate_api_key(self.hass, api_key)
                if not is_valid:
                    errors["base"] = "invalid_api_key"

            # Validate forecast entity
            if not errors and forecast_entity:
                if not self._is_valid_forecast_entity(self.hass, forecast_entity):
                    errors["base"] = "invalid_forecast_entity"

            if not errors:
                # Unique ID = API key
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
                        "forecast_entity_id": forecast_entity,
                        "sensors": [],  # empty → user will configure in OptionsFlow
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=await self._get_schema(),
            errors=errors,
        )

    async def _get_schema(self):
        """Return the input form schema."""
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
                vol.Required(CONF_UPDATE_INTERVAL, default=5): vol.All(int, vol.Range(min=1, max=30)),
                vol.Optional("forecast_entity_id", default=None): vol.In(
                    [None] + weather_entities
                ),
            }
        )

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

        # API may return list or dict
        if isinstance(data, list) and len(data) > 0:
            return True

        if isinstance(data, dict) and ("data" in data or "Zprava" in data):
            return True

        return False

    def _is_valid_forecast_entity(self, hass: HomeAssistant, entity_id: str) -> bool:
        """Check if selected entity exists and is a weather entity."""
        if not entity_id:
            return True

        state = hass.states.get(entity_id)
        if not state:
            return False

        return state.domain == "weather"

    # ------------------------------------------------------------
    # OPTIONS FLOW
    # ------------------------------------------------------------

    def async_get_options_flow(self, config_entry):
        return PocasimeteoOptionsFlow(config_entry)


class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options for PočasíMeteo."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Initial step: choose sensors."""
        sensors = self.config_entry.options.get("sensors", [])

        # Extract existing sensor IDs
        existing_ids = [s["id"] for s in sensors]

        schema = vol.Schema(
            {
                vol.Optional(
                    "sensor_list",
                    default=existing_ids or DEFAULT_SENSOR_LIST,
                ): vol.All([str]),
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

            # Store temporarily
            self._sensor_ids = sensor_list

            return await self.async_step_types()

        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_types(self, user_input=None):
        """Step 2: assign types to sensors."""
        sensor_ids = getattr(self, "_sensor_ids", [])

        schema_dict = {}
        for sid in sensor_ids:
            schema_dict[
                vol.Required(f"type_{sid}", default="primary")
            ] = vol.In(["primary", "secondary"])

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            self._sensor_types = {
                sid: user_input[f"type_{sid}"] for sid in sensor_ids
            }
            return await self.async_step_order()

        return self.async_show_form(step_id="types", data_schema=schema)

    async def async_step_order(self, user_input=None):
        """Step 3: assign order to sensors."""
        sensor_ids = getattr(self, "_sensor_ids", [])

        schema_dict = {}
        for sid in sensor_ids:
            schema_dict[
                vol.Required(f"order_{sid}", default=1)
            ] = vol.All(int, vol.Range(min=1, max=99))

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
