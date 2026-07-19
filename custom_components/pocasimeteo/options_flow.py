"""Options flow for PočasíMeteo integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DEFAULT_SENSORS_OPTIONS, DEFAULT_ALL_SENSOR_IDS


class PocasimeteoOptionsFlow(config_entries.OptionsFlow):
    """Handle options for PočasíMeteo."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self._sensor_ids: list[str] = []
        self._sensor_types: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Step 1: Select sensors
    # ------------------------------------------------------------------
    async def async_step_init(self, user_input=None) -> FlowResult:
        """Initial step: choose which sensors are active."""
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

            # Remove duplicates while preserving order
            sensor_list = list(dict.fromkeys(sensor_list))

            self._sensor_ids = sensor_list
            return await self.async_step_types()

        return self.async_show_form(step_id="init", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 2: Assign sensor types
    # ------------------------------------------------------------------
    async def async_step_types(self, user_input=None) -> FlowResult:
        """Second step: assign primary/secondary type to each sensor."""
        sensor_ids = self._sensor_ids

        schema_dict: dict[vol.Marker, object] = {}
        for sid in sensor_ids:
            # Default type based on known definitions; unknown sensors default to secondary
            default_type = "secondary"
            if sid in DEFAULT_ALL_SENSOR_IDS:
                # Look up default type from DEFAULT_SENSORS_OPTIONS
                for s in DEFAULT_SENSORS_OPTIONS:
                    if s["id"] == sid:
                        default_type = s["type"]
                        break

            schema_dict[vol.Required(f"type_{sid}", default=default_type)] = vol.In(
                ["primary", "secondary"]
            )

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            self._sensor_types = {sid: user_input[f"type_{sid}"] for sid in sensor_ids}
            return await self.async_step_order()

        return self.async_show_form(step_id="types", data_schema=schema)

    # ------------------------------------------------------------------
    # Step 3: Assign ordering
    # ------------------------------------------------------------------
    async def async_step_order(self, user_input=None) -> FlowResult:
        """Final step: assign ordering index to each sensor."""
        sensor_ids = self._sensor_ids

        schema_dict: dict[vol.Marker, object] = {}
        for sid in sensor_ids:
            schema_dict[vol.Required(f"order_{sid}", default=1)] = vol.All(
                int, vol.Range(min=1, max=999)
            )

        schema = vol.Schema(schema_dict)

        if user_input is not None:
            sensors_final: list[dict[str, object]] = []
            for sid in sensor_ids:
                sensors_final.append(
                    {
                        "id": sid,
                        "type": self._sensor_types[sid],
                        "order": user_input[f"order_{sid}"],
                    }
                )

            # Single canonical options structure: sensors list
            return self.async_create_entry(
                title="Senzory",
                data={"sensors": sensors_final},
            )

        return self.async_show_form(step_id="order", data_schema=schema)
