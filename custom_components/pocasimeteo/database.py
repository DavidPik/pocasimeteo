"""Database layer for PočasíMeteo integration."""

from __future__ import annotations

import logging
import sqlite3

from pathlib import Path
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    DB_FILENAME,
    DB_SCHEMA_VERSION,
    DB_TABLE_STATIONS,
    DB_TABLE_MEASUREMENTS,
    DB_TABLE_SENSOR_METADATA,
)

_LOGGER = logging.getLogger(__name__)


class PocasimeteoDatabase:
    """
    Databázová vrstva integrace.

    Odpovědnosti:
    - vytvoření databáze
    - správa schématu
    - ukládání měření
    - načítání historie
    - načítání metadat senzorů
    - diagnostika databáze

    Neprovádí:
    - výpočty statistik
    - meteorologickou logiku
    - přípravu frontend payloadů
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

        storage_dir = Path(hass.config.path(".storage")) / DOMAIN
        storage_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = storage_dir / DB_FILENAME

        self._initialized = False

    # ============================================================
    # SCHÉMA DATABÁZE
    # ============================================================

    async def async_initialize(self) -> None:
        """Inicializace databáze."""

        if self._initialized:
            return

        await self.hass.async_add_executor_job(
            self._create_database
        )

        self._initialized = True

    def _create_database(self) -> None:
        """Vytvoření schématu databáze."""

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    schema_version INTEGER NOT NULL
                )
                """
            )

            count = conn.execute(
                "SELECT COUNT(*) FROM schema_info"
            ).fetchone()[0]

            if count == 0:
                conn.execute(
                    """
                    INSERT INTO schema_info (
                        schema_version
                    )
                    VALUES (?)
                    """,
                    (DB_SCHEMA_VERSION,),
                )

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {DB_TABLE_STATIONS} (
                    station_id TEXT PRIMARY KEY,
                    station_name TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {DB_TABLE_SENSOR_METADATA} (
                    station_id TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    sensor_name TEXT,
                    sensor_type TEXT,

                    PRIMARY KEY (
                        station_id,
                        sensor_id
                    )
                )
                """
            )

            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {DB_TABLE_MEASUREMENTS} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    station_id TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,

                    timestamp TEXT NOT NULL,
                    value REAL,

                    UNIQUE(
                        station_id,
                        sensor_id,
                        timestamp
                    )
                )
                """
            )

            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS
                idx_measurements_lookup
                ON {DB_TABLE_MEASUREMENTS}
                (
                    station_id,
                    sensor_id,
                    timestamp
                )
                """
            )

            conn.commit()

    # ============================================================
    # STANICE
    # ============================================================

    async def async_register_station(
        self,
        station_id: str,
        station_name: str,
    ) -> None:
        """
        Registrace stanice.

        Logika databáze.
        """

        await self.hass.async_add_executor_job(
            self._register_station,
            station_id,
            station_name,
        )

    def _register_station(
        self,
        station_id: str,
        station_name: str,
    ) -> None:

        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO
                {DB_TABLE_STATIONS}
                (
                    station_id,
                    station_name,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    station_id,
                    station_name,
                    now,
                    now,
                ),
            )

            conn.commit()

    # ============================================================
    # METADATA SENZORŮ
    # ============================================================

    async def async_register_sensor(
        self,
        station_id: str,
        sensor_id: str,
        sensor_name: str,
        sensor_type: str,
    ) -> None:
        """
        Registrace senzoru.

        Logika databáze.
        """

        await self.hass.async_add_executor_job(
            self._register_sensor,
            station_id,
            sensor_id,
            sensor_name,
            sensor_type,
        )

    def _register_sensor(
        self,
        station_id: str,
        sensor_id: str,
        sensor_name: str,
        sensor_type: str,
    ) -> None:

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO
                {DB_TABLE_SENSOR_METADATA}
                (
                    station_id,
                    sensor_id,
                    sensor_name,
                    sensor_type
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    station_id,
                    sensor_id,
                    sensor_name,
                    sensor_type,
                ),
            )

            conn.commit()

    # ============================================================
    # UKLÁDÁNÍ MĚŘENÍ
    # ============================================================

    async def async_store_measurements(
        self,
        station_id: str,
        measurements: list[dict[str, Any]],
    ) -> None:
        """
        Hromadné uložení měření.

        Logika databáze.
        """

        await self.hass.async_add_executor_job(
            self._store_measurements,
            station_id,
            measurements,
        )

    def _store_measurements(
        self,
        station_id: str,
        measurements: list[dict[str, Any]],
    ) -> None:

        rows = []

        for item in measurements:

            sensor_id = item.get("sensor_id")
            timestamp = item.get("timestamp")
            value = item.get("value")

            if not sensor_id:
                continue

            if not timestamp:
                continue

            rows.append(
                (
                    station_id,
                    sensor_id,
                    timestamp,
                    value,
                )
            )

        if not rows:
            return

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                f"""
                INSERT OR IGNORE INTO
                {DB_TABLE_MEASUREMENTS}
                (
                    station_id,
                    sensor_id,
                    timestamp,
                    value
                )
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )

            conn.commit()

    # ============================================================
    # HISTORIE
    # ============================================================

    async def async_get_sensor_history(
        self,
        station_id: str,
        sensor_id: str,
        start_timestamp: str,
    ) -> list[tuple[str, float]]:
        """
        Načtení historie senzoru.

        Logika databáze.
        """

        return await self.hass.async_add_executor_job(
            self._get_sensor_history,
            station_id,
            sensor_id,
            start_timestamp,
        )

    def _get_sensor_history(
        self,
        station_id: str,
        sensor_id: str,
        start_timestamp: str,
    ) -> list[tuple[str, float]]:

        with sqlite3.connect(self.db_path) as conn:

            rows = conn.execute(
                f"""
                SELECT
                    timestamp,
                    value
                FROM
                    {DB_TABLE_MEASUREMENTS}
                WHERE
                    station_id = ?
                    AND sensor_id = ?
                    AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (
                    station_id,
                    sensor_id,
                    start_timestamp,
                ),
            ).fetchall()

        return rows

    # ============================================================
    # DIAGNOSTIKA
    # ============================================================

    async def async_get_diagnostics(self) -> dict[str, Any]:
        """
        Diagnostika databáze.

        Logika databáze.
        """

        return await self.hass.async_add_executor_job(
            self._get_diagnostics
        )

    def _get_diagnostics(self) -> dict[str, Any]:

        db_size = (
            self.db_path.stat().st_size
            if self.db_path.exists()
            else 0
        )

        with sqlite3.connect(self.db_path) as conn:

            station_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {DB_TABLE_STATIONS}
                """
            ).fetchone()[0]

            measurement_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {DB_TABLE_MEASUREMENTS}
                """
            ).fetchone()[0]

            last_insert = conn.execute(
                f"""
                SELECT MAX(timestamp)
                FROM {DB_TABLE_MEASUREMENTS}
                """
            ).fetchone()[0]

        return {
            "schema_version": DB_SCHEMA_VERSION,
            "database_path": str(self.db_path),
            "database_size": db_size,
            "station_count": station_count,
            "measurement_count": measurement_count,
            "last_insert": last_insert,
        }
