"""Data update coordinator for PočasíMeteo integration."""

from __future__ import annotations

import logging
import asyncio
import math
from collections import Counter
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import aiohttp_client

# Recorder components pro moderní DB schéma Home Assistenta
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import States, StatesMeta, StateAttributes
from sqlalchemy import select

from .const import (
    DOMAIN,
    API_URL_BASE,
    CONF_API_KEY,
    CONF_UPDATE_INTERVAL,
    CONF_SENSORS,
    CONF_STATION,
    SENSOR_DEFINITIONS,
    DEFAULT_SENSOR_OPTIONS,
    get_dynamic_sensor_meta,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Koordinátor odpovědný za stahování dat, plnění mezer v historii databáze
    a výpočet klouzavých 24h statistik pro potřeby frontendové karty.
    """

    # -------------------------------------------------------------------------
    # Recorder helpers
    # -------------------------------------------------------------------------

    async def _history_exists(self, entity_id: str, ts: datetime) -> bool:
        rec = get_instance(self.hass)

        def _check():
            with rec.get_session() as session:
                q = select(States).where(
                    States.entity_id == entity_id,
                    States.last_changed == ts,
                )
                return session.execute(q).first() is not None

        return await self.hass.async_add_executor_job(_check)

    async def _insert_history_point(self, entity_id: str, value, ts: datetime):
        rec = get_instance(self.hass)

        def _insert():
            with rec.get_session() as session:
                # Metadata
                meta_row = session.execute(
                    select(StatesMeta).where(StatesMeta.entity_id == entity_id)
                ).scalar_one_or_none()

                if not meta_row:
                    meta_row = StatesMeta(entity_id=entity_id)
                    session.add(meta_row)
                    session.flush()

                metadata_id = meta_row.metadata_id

                # Attributes
                attr_row = session.execute(
                    select(StateAttributes).where(StateAttributes.shared_attrs == "{}")
                ).scalar_one_or_none()

                if not attr_row:
                    attr_row = StateAttributes(shared_attrs="{}")
                    session.add(attr_row)
                    session.flush()

                attributes_id = attr_row.attributes_id

                # State
                row = States(
                    entity_id=entity_id,
                    metadata_id=metadata_id,
                    attributes_id=attributes_id,
                    state=str(value),
                    last_changed=ts,
                    last_updated=ts,
                )
                session.add(row)
                session.commit()

        await self.hass.async_add_executor_job(_insert)

    # -------------------------------------------------------------------------
    # Import historie – příprava fronty + background worker
    # -------------------------------------------------------------------------

    async def _import_history(self, measurements: list[dict]):
        """Připraví historii z JSONu API do fronty pro pomalý import do databáze."""

        # Seřadíme historii podle času
        sorted_measurements = sorted(
            measurements,
            key=lambda m: datetime.fromisoformat(m["Datum"]),
        )

        previous_rain = None
        previous_ts = None

        # Výpočet intenzity srážek
        for m in sorted_measurements:
            ts_raw = m.get("Datum")
            if not ts_raw:
                continue
