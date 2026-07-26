"""Config flow for PočasíMeteo integration."""

from __future__ import annotations

import logging
import async_timeout
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
    API_URL_BASE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options updates (reconfiguration) via UI."""

    # Metoda __init__ byla kompletně odebrána, aby nevznikal konflikt v HA Core.
    # self.config_entry je přesto plně k dispozici díky základní třídě.

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the options menu."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        registry = er.async_get(self.hass)
        weather_entities = sorted(
            [
                entity.entity_id
                for entity in registry.entities.values()
                if entity.entity_id.startswith("weather.")
            ]
        )

        # Bezpečné vytažení aktuálního intervalu z automatického self.config_entry
        current_interval = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL, 
            self.config_entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)
        )
        
        current_forecast = self.config_entry.options.get("forecast_entity_id")
        if current_forecast not in weather_entities:
            current_forecast = None

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UPDATE_INTERVAL, default=current_interval): vol.All(int, vol.Range(min=1, max=30)),
                    vol.Optional("forecast_entity_id", default=current_forecast): vol.In([None] + weather_entities),
                }
            ),
        )


class PocasimeteoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial configuration flow for PočasíMeteo."""

    VERSION = 3

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle user step for adding a new station."""
        errors: dict[str, str] = {}

        if user_input is not None:
            station_name = user_input[CONF_STATION]
            api_key = user_input[CONF_API_KEY].strip()
            interval = user_input[CONF_UPDATE_INTERVAL]
            forecast_entity = user_input.get("forecast_entity_id")

            if not await self._async_validate_api_key(self.hass, api_key):
                errors["base"] = "invalid_api_key"

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
                        CONF_UPDATE_INTERVAL: interval,
                        "forecast_entity_id": forecast_entity or "",
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=await self._get_schema(user_input),
            errors=errors,
        )

    async def _get_schema(self, user_input=None) -> vol.Schema:
        """Generate schema dynamically including available weather entities."""
        registry = er.async_get(self.hass)
        weather_entities = sorted(
            [
                entity.entity_id
                for entity in registry.entities.values()
                if entity.entity_id.startswith("weather.")
            ]
        )

        current_station = ""
        current_key = ""
        current_interval = DEFAULT_UPDATE_INTERVAL_MINUTES
        current_forecast = None

        if user_input:
            current_station = user_input.get(CONF_STATION, "")
            current_key = user_input.get(CONF_API_KEY, "")
            current_interval = user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES)
            current_forecast = user_input.get("forecast_entity_id")

        return vol.Schema(
            {
                vol.Required(CONF_STATION, default=current_station): str,
                vol.Required(CONF_API_KEY, default=current_key): str,
                vol.Required(CONF_UPDATE_INTERVAL, default=current_interval): vol.All(int, vol.Range(min=1, max=30)),
                vol.Optional("forecast_entity_id", default=current_forecast): vol.In([None] + weather_entities),
            }
        )

    async def _async_validate_api_key(self, hass: HomeAssistant, api_key: str) -> bool:
        """Validate API key by calling PočasíMeteo API."""
        url = f"{API_URL_BASE}?KlicApi={api_key}"

        try:
            session = aiohttp_client.async_get_clientsession(hass)
            async with async_timeout.timeout(10):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    return isinstance(data, (dict, list))
        except Exception as err:
            _LOGGER.error("API validation exception occurred: %s", err)
            return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> PocasimeteoOptionsFlow:
        """Link to Options Flow handler."""
        return PocasimeteoOptionsFlow(config_entry)
