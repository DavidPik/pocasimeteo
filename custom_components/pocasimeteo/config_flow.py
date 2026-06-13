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
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .const import (
    DOMAIN,
    CONF_STATION,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    API_URL_TEMPLATE,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PočasíMeteo."""

    VERSION = 1

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
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=await self._get_schema(),
            errors=errors,
        )

    async def _get_schema(self):
        """Return the input form schema."""
        # Load all weather entities for forecast selection
        registry = async_get_entity_registry(self.hass)
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
