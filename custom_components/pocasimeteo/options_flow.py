from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN

class PocasimeteoOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(
                    "update_interval",
                    default=options.get("update_interval", 60)
                ): int,

                vol.Optional(
                    "primary_sensors",
                    default="\n".join(options.get("primary_sensors", []))
                ): str,

                vol.Optional(
                    "secondary_sensors",
                    default="\n".join(options.get("secondary_sensors", []))
                ): str,

                vol.Optional(
                    "forecast_entity_id",
                    default=options.get("forecast_entity_id", "")
                ): str,
            })
        )
