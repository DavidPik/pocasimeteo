import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN

DEFAULT_PRIMARY = [
    "TeplotaVnejsi",
    "VlhkostVnejsi",
    "TlakRel",
    "SlunZareni",
    "Vitr",
    "VitrNarazy",
    "VitrSmer",
]

DEFAULT_SECONDARY = [
    "TeplotaVnitrni",
    "VlhkostVnitrni",
]


def _safe_list(value):
    """Convert None or string to list safely."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [s.strip() for s in value.splitlines() if s.strip()]
    return []


class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options for PočasíMeteo."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Step 1: choose sensors."""
        options = self.config_entry.options

        sensors = options.get("sensors", [])
        existing_ids = [s["id"] for s in sensors]

        default_ids = existing_ids or (DEFAULT_PRIMARY + DEFAULT_SECONDARY)

        schema = vol.Schema(
            {
                vol.Optional("sensor_list", default=default_ids): vol.All([str]),
                vol.Optional("add_custom_sensor", default=""): str,
            }
        )

        if user_input is not None:
            sensor_list = user_input.get("sensor_list", [])
            custom = user_input.get("add_custom_sensor", "").strip()

            if custom:
                sensor_list.append(custom)

            sensor_list = list(dict.fromkeys(sensor_list))

            self._sensor_ids = sensor_list

            return await self.async_step_types()

        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_types(self, user_input=None):
        """Step 2: assign types to sensors."""
        sensor_ids = getattr(self, "_sensor_ids", [])

        schema_dict = {}
        for sid in sensor_ids:
            default_type = "primary" if sid in DEFAULT_PRIMARY else "secondary"
            schema_dict[
                vol.Required(f"type_{sid}", default=default_type)
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
            primary_list = []
            secondary_list = []

            for sid in sensor_ids:
                typ = self._sensor_types[sid]
                order = user_input[f"order_{sid}"]

                sensors_final.append(
                    {
                        "id": sid,
                        "type": typ,
                        "order": order,
                    }
                )

                if typ == "primary":
                    primary_list.append(sid)
                else:
                    secondary_list.append(sid)

            return self.async_create_entry(
                title="Senzory",
                data={
                    "sensors": sensors_final,
                    "primary_sensors": primary_list,
                    "secondary_sensors": secondary_list,
                },
            )

        return self.async_show_form(step_id="order", data_schema=schema)
