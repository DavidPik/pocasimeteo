from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN


def _safe_list(value):
    """Convert None or string to list safely."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [s.strip() for s in value.splitlines() if s.strip()]
    return []


class PocasimeteoOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow for PočasíMeteo."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        options = self.config_entry.options

        # --- SAFE DEFAULTS ---
        update_interval_default = options.get("update_interval", 60)
        primary_default = _safe_list(options.get("primary_sensors"))
        secondary_default = _safe_list(options.get("secondary_sensors"))
        forecast_default = options.get("forecast_entity_id", "")

        if user_input is not None:
            # Convert multiline text → list
            primary = _safe_list(user_input.get("primary_sensors"))
            secondary = _safe_list(user_input.get("secondary_sensors"))

            new_options = {
                "update_interval": user_input.get("update_interval", 60),
                "primary_sensors": primary,
                "secondary_sensors": secondary,
                "forecast_entity_id": user_input.get("forecast_entity_id", "")
            }

            return self.async_create_entry(title="", data=new_options)

        # --- SHOW FORM ---
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(
                    "update_interval",
                    default=update_interval_default
                ): int,

                vol.Optional(
                    "primary_sensors",
                    default="\n".join(primary_default)
                ): str,

                vol.Optional(
                    "secondary_sensors",
                    default="\n".join(secondary_default)
                ): str,

                vol.Optional(
                    "forecast_entity_id",
                    default=forecast_default
                ): str,
            })
        )
