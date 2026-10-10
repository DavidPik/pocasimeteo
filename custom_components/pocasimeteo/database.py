"""Persistence layer for PočasíMeteo measurements.

The coordinator depends on the asynchronous ``PocasimeteoDatabase`` facade.
The facade delegates blocking work to an injected backend. SQLite is the
current backend; a future database engine can implement the same protocol
without changing measurement transformation or coordinator logic.
"""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .const import (
    DB_FILENAME,
    DB_SCHEMA_VERSION,
    DB_TABLE_MEASUREMENTS,
    DB_TABLE_SENSOR_METADATA,
    DB_TABLE_STATIONS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_SCHEMA_VERSION = DB_SCHEMA_VERSION
_TABLE_STATIONS = DB_TABLE_STATIONS
_TABLE_MEASUREMENTS = DB_TABLE_MEASUREMENTS
_TABLE_SENSOR_METADATA = DB_TABLE_SENSOR_METADATA


class DatabaseBackend(Protocol):
    """Synchronous storage contract implemented by each database engine."""

    def initialize(self) -> None: ...

    def store_dataset(
        self,
        station_id: str,
        station_name: str,
        sensor_metadata: list[dict[str, Any]],
        measurements: list[dict[str, Any]],
    ) -> None: ...

    def register_station(self, station_id: str, station_name: str) -> None: ...

    def register_sensor(self, sensor: dict[str, Any], station_id: str) -> None: ...

    def store_measurements(
        self, station_id: str, measurements: list[dict[str, Any]]
    ) -> None: ...

    def get_sensor_history(
        self,
        station_id: str,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str | None = None,
    ) -> list[tuple[str, float]]: ...

    def get_diagnostics(self) -> dict[str, Any]: ...


class SQLiteDatabaseBackend:
    """SQLite implementation isolated behind the database backend contract."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self):
        """Open a connection and always close it after the transaction."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            with conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create the schema or reject a database with an unknown version."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_info "
                "(schema_version INTEGER NOT NULL)"
            )
            versions = conn.execute(
                "SELECT schema_version FROM schema_info"
            ).fetchall()
            if not versions:
                conn.execute(
                    "INSERT INTO schema_info (schema_version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            elif len(versions) != 1 or versions[0][0] != _SCHEMA_VERSION:
                raise RuntimeError(
                    "Unsupported PočasíMeteo database schema version: "
                    f"{versions!r} (expected {_SCHEMA_VERSION})"
                )

            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {_TABLE_STATIONS} (
                    station_id TEXT PRIMARY KEY,
                    station_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {_TABLE_SENSOR_METADATA} (
                    station_id TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    sensor_name TEXT NOT NULL,
                    sensor_type TEXT NOT NULL,
                    PRIMARY KEY (station_id, sensor_id)
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {_TABLE_MEASUREMENTS} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    value REAL NOT NULL,
                    UNIQUE (station_id, sensor_id, timestamp)
                )"""
            )
            conn.execute(
                f"""CREATE INDEX IF NOT EXISTS idx_measurements_lookup
                    ON {_TABLE_MEASUREMENTS}
                    (station_id, sensor_id, timestamp)"""
            )

    def store_dataset(
        self,
        station_id: str,
        station_name: str,
        sensor_metadata: list[dict[str, Any]],
        measurements: list[dict[str, Any]],
    ) -> None:
        """Persist station metadata and a measurement batch atomically."""
        now = datetime.now().astimezone().isoformat()
        measurement_rows = self._measurement_rows(station_id, measurements)

        with self._connection() as conn:
            conn.execute(
                f"""INSERT INTO {_TABLE_STATIONS}
                    (station_id, station_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(station_id) DO UPDATE SET
                        station_name = excluded.station_name,
                        updated_at = excluded.updated_at""",
                (station_id, station_name, now, now),
            )
            conn.executemany(
                f"""INSERT INTO {_TABLE_SENSOR_METADATA}
                    (station_id, sensor_id, sensor_name, sensor_type)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(station_id, sensor_id) DO UPDATE SET
                        sensor_name = excluded.sensor_name,
                        sensor_type = excluded.sensor_type""",
                [
                    (
                        station_id,
                        sensor["sensor_id"],
                        sensor["sensor_name"],
                        sensor["sensor_type"],
                    )
                    for sensor in sensor_metadata
                    if sensor.get("sensor_id")
                    and sensor.get("sensor_name")
                    and sensor.get("sensor_type")
                ],
            )
            conn.executemany(
                f"""INSERT OR IGNORE INTO {_TABLE_MEASUREMENTS}
                    (station_id, sensor_id, timestamp, value)
                    VALUES (?, ?, ?, ?)""",
                measurement_rows,
            )

    @staticmethod
    def _measurement_rows(
        station_id: str, measurements: list[dict[str, Any]]
    ) -> list[tuple[str, str, str, float]]:
        rows: list[tuple[str, str, str, float]] = []
        for item in measurements:
            sensor_id = item.get("sensor_id")
            timestamp = item.get("timestamp")
            value = item.get("value")
            if not sensor_id or not timestamp or value is None:
                continue

            try:
                parsed_timestamp = datetime.fromisoformat(
                    str(timestamp).replace("Z", "+00:00")
                )
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue

            if not math.isfinite(numeric_value):
                continue

            # Keep the API's normalized ISO timestamp unchanged. The source
            # uses one timestamp format consistently for a given station.
            rows.append(
                (station_id, str(sensor_id), parsed_timestamp.isoformat(), numeric_value)
            )
        return rows

    def register_station(self, station_id: str, station_name: str) -> None:
        self.store_dataset(station_id, station_name, [], [])

    def register_sensor(self, sensor: dict[str, Any], station_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                f"""INSERT INTO {_TABLE_SENSOR_METADATA}
                    (station_id, sensor_id, sensor_name, sensor_type)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(station_id, sensor_id) DO UPDATE SET
                        sensor_name = excluded.sensor_name,
                        sensor_type = excluded.sensor_type""",
                (
                    station_id,
                    sensor["sensor_id"],
                    sensor["sensor_name"],
                    sensor["sensor_type"],
                ),
            )

    def store_measurements(
        self, station_id: str, measurements: list[dict[str, Any]]
    ) -> None:
        self.store_dataset(station_id, station_id, [], measurements)

    def get_sensor_history(
        self,
        station_id: str,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str | None = None,
    ) -> list[tuple[str, float]]:
        with self._connection() as conn:
            end_clause = " AND timestamp <= ?" if end_timestamp is not None else ""
            parameters = [station_id, sensor_id, start_timestamp]
            if end_timestamp is not None:
                parameters.append(end_timestamp)
            rows = conn.execute(
                f"""SELECT timestamp, value FROM {_TABLE_MEASUREMENTS}
                    WHERE station_id = ? AND sensor_id = ? AND timestamp >= ?
                    {end_clause}
                    ORDER BY timestamp ASC""",
                parameters,
            ).fetchall()
        return [(str(timestamp), float(value)) for timestamp, value in rows]

    def get_diagnostics(self) -> dict[str, Any]:
        with self._connection() as conn:
            station_count = conn.execute(
                f"SELECT COUNT(*) FROM {_TABLE_STATIONS}"
            ).fetchone()[0]
            measurement_count = conn.execute(
                f"SELECT COUNT(*) FROM {_TABLE_MEASUREMENTS}"
            ).fetchone()[0]
            last_insert = conn.execute(
                f"SELECT MAX(timestamp) FROM {_TABLE_MEASUREMENTS}"
            ).fetchone()[0]

        return {
            "backend": "sqlite",
            "schema_version": _SCHEMA_VERSION,
            "database_path": str(self.db_path),
            "database_size": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "station_count": station_count,
            "measurement_count": measurement_count,
            "last_insert": last_insert,
        }


class PocasimeteoDatabase:
    """Asynchronous Home Assistant facade for the configured DB backend."""

    def __init__(
        self,
        hass: Any,
        *,
        domain: str = DOMAIN,
        filename: str = DB_FILENAME,
        backend: DatabaseBackend | None = None,
    ) -> None:
        self.hass = hass
        self.db_path = Path(hass.config.path(".storage")) / domain / filename
        self._backend: DatabaseBackend = backend or SQLiteDatabaseBackend(self.db_path)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def async_initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await self.hass.async_add_executor_job(self._backend.initialize)
            self._initialized = True

    async def async_store_dataset(
        self,
        station_id: str,
        station_name: str,
        sensor_metadata: list[dict[str, Any]],
        measurements: list[dict[str, Any]],
    ) -> None:
        await self.async_initialize()
        await self.hass.async_add_executor_job(
            self._backend.store_dataset,
            station_id,
            station_name,
            sensor_metadata,
            measurements,
        )

    async def async_register_station(self, station_id: str, station_name: str) -> None:
        await self.async_initialize()
        await self.hass.async_add_executor_job(
            self._backend.register_station, station_id, station_name
        )

    async def async_register_sensor(
        self, station_id: str, sensor_id: str, sensor_name: str, sensor_type: str
    ) -> None:
        await self.async_initialize()
        await self.hass.async_add_executor_job(
            self._backend.register_sensor,
            {
                "sensor_id": sensor_id,
                "sensor_name": sensor_name,
                "sensor_type": sensor_type,
            },
            station_id,
        )

    async def async_store_measurements(
        self, station_id: str, measurements: list[dict[str, Any]]
    ) -> None:
        await self.async_initialize()
        await self.hass.async_add_executor_job(
            self._backend.store_measurements, station_id, measurements
        )

    async def async_get_sensor_history(
        self,
        station_id: str,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str | None = None,
    ) -> list[tuple[str, float]]:
        await self.async_initialize()
        return await self.hass.async_add_executor_job(
            self._backend.get_sensor_history,
            station_id,
            sensor_id,
            start_timestamp,
            end_timestamp,
        )

    async def async_get_diagnostics(self) -> dict[str, Any]:
        await self.async_initialize()
        return await self.hass.async_add_executor_job(self._backend.get_diagnostics)
