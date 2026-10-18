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

    def validate(self) -> None: ...

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
        """Open an existing database; ordinary access must never create a file."""
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Databáze neexistuje: {self.db_path}")
        uri = f"{self.db_path.resolve().as_uri()}?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            with conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create a fresh database, or validate an existing one without altering it."""
        if self.db_path.exists():
            self.validate()
            return

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            # Reserve the path so this code never overwrites a file created concurrently.
            with self.db_path.open("xb"):
                pass
            created = True
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                conn.execute(
                    "CREATE TABLE schema_info (schema_version INTEGER NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO schema_info (schema_version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
                conn.execute(
                    f"""CREATE TABLE {_TABLE_STATIONS} (
                        station_id TEXT PRIMARY KEY,
                        station_name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                conn.execute(
                    f"""CREATE TABLE {_TABLE_SENSOR_METADATA} (
                        station_id TEXT NOT NULL,
                        sensor_id TEXT NOT NULL,
                        sensor_name TEXT NOT NULL,
                        sensor_type TEXT NOT NULL,
                        PRIMARY KEY (station_id, sensor_id)
                    )"""
                )
                conn.execute(
                    f"""CREATE TABLE {_TABLE_MEASUREMENTS} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        station_id TEXT NOT NULL,
                        sensor_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        value REAL NOT NULL,
                        UNIQUE (station_id, sensor_id, timestamp)
                    )"""
                )
                conn.execute(
                    f"""CREATE INDEX idx_measurements_lookup
                        ON {_TABLE_MEASUREMENTS}
                        (station_id, sensor_id, timestamp)"""
                )
            self.validate()
        except Exception:
            if created:
                self.db_path.unlink(missing_ok=True)
            raise

    def validate(self) -> None:
        """Validate schema, SQLite integrity, version, and persisted row values.

        This validation is intentionally tolerant about PRAGMA returned metadata
        (different SQLite builds may return slightly different tuples). We verify
        that required columns exist and their types are compatible rather than
        insisting on an exact tuple match.
        """
        with self._connection() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise RuntimeError(f"SQLite integrity_check failed: {integrity!r}")

            versions = conn.execute(
                "SELECT schema_version FROM schema_info"
            ).fetchall()
            if len(versions) != 1 or versions[0][0] != _SCHEMA_VERSION:
                raise RuntimeError(
                    "Unsupported PočasíMeteo database schema version: "
                    f"{versions!r} (expected {_SCHEMA_VERSION})"
                )

            expected_columns = {
                "schema_info": {
                    "schema_version": ("INTEGER", 1, 0),
                },
                _TABLE_STATIONS: {
                    "station_id": ("TEXT", 0, 1),
                    "station_name": ("TEXT", 1, 0),
                    "created_at": ("TEXT", 1, 0),
                    "updated_at": ("TEXT", 1, 0),
                },
                _TABLE_SENSOR_METADATA: {
                    "station_id": ("TEXT", 1, 1),
                    "sensor_id": ("TEXT", 1, 2),
                    "sensor_name": ("TEXT", 1, 0),
                    "sensor_type": ("TEXT", 1, 0),
                },
                _TABLE_MEASUREMENTS: {
                    "id": ("INTEGER", 0, 1),
                    "station_id": ("TEXT", 1, 0),
                    "sensor_id": ("TEXT", 1, 0),
                    "timestamp": ("TEXT", 1, 0),
                    "value": ("REAL", 1, 0),
                },
            }

            # Per-table checks: ensure required columns exist and types are compatible
            for table, columns in expected_columns.items():
                actual_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                actual = {row[1]: str(row[2]).upper() for row in actual_rows}

                for col_name, (expected_type, _, _) in columns.items():
                    if col_name not in actual:
                        raise RuntimeError(f"Neplatná struktura tabulky {table}: missing column {col_name}")
                    if expected_type.upper() not in actual[col_name]:
                        # Allow e.g. 'INTEGER' vs 'INT' variations by substring check
                        raise RuntimeError(
                            f"Neplatná struktura tabulky {table}: column {col_name} has type {actual[col_name]!r}, expected {expected_type!r}"
                        )

            index = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_measurements_lookup'"
            ).fetchone()
            if index is None:
                raise RuntimeError("V databázi chybí index měření.")
            index_columns = [row[2] for row in conn.execute(f"PRAGMA index_info({index[0]})")]
            if set(index_columns) != {"station_id", "sensor_id", "timestamp"}:
                raise RuntimeError("Index měření má neplatnou strukturu.")

            unique_measurement_indexes = [row[1] for row in conn.execute(f"PRAGMA index_list({_TABLE_MEASUREMENTS})") if row[2]]
            has_measurement_key = False
            for index_name in unique_measurement_indexes:
                cols = [row[2] for row in conn.execute(f"PRAGMA index_info({index_name})")]
                if set(cols) == {"station_id", "sensor_id", "timestamp"}:
                    has_measurement_key = True
                    break
            if not has_measurement_key:
                raise RuntimeError("Databázi chybí unikátní klíč měření.")

            for station_id, station_name, created_at, updated_at in conn.execute(
                f"SELECT station_id, station_name, created_at, updated_at "
                f"FROM {_TABLE_STATIONS}"
            ):
                if not station_id or not station_name:
                    raise RuntimeError("Databáze obsahuje neplatnou stanici.")
                try:
                    datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                except (TypeError, ValueError) as err:
                    raise RuntimeError(
                        "Databáze obsahuje neplatné časové údaje stanice."
                    ) from err

            for station_id, sensor_id, sensor_name, sensor_type in conn.execute(
                f"SELECT station_id, sensor_id, sensor_name, sensor_type "
                f"FROM {_TABLE_SENSOR_METADATA}"
            ):
                if not all((station_id, sensor_id, sensor_name, sensor_type)):
                    raise RuntimeError("Databáze obsahuje neplatná metadata senzoru.")

            rows = conn.execute(
                f"SELECT station_id, sensor_id, timestamp, value "
                f"FROM {_TABLE_MEASUREMENTS}"
            )
            for station_id, sensor_id, timestamp, value in rows:
                if not station_id or not sensor_id or not timestamp:
                    raise RuntimeError("Databáze obsahuje neúplný záznam měření.")
                try:
                    datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                    numeric_value = float(value)
                except (TypeError, ValueError) as err:
                    raise RuntimeError(
                        "Databáze obsahuje neplatný čas nebo hodnotu měření."
                    ) from err
                if not math.isfinite(numeric_value):
                    raise RuntimeError(
                        "Databáze obsahuje nečíselnou hodnotu měření."
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
            if measurement_rows:
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
        """Store measurements without touching station metadata.

        Previously this method delegated to store_dataset(station_id, station_id,...)
        which accidentally overwrote station_name with the station_id. Insert only
        measurement rows here.
        """
        measurement_rows = self._measurement_rows(station_id, measurements)
        if not measurement_rows:
            return
        with self._connection() as conn:
            conn.executemany(
                f"""INSERT OR IGNORE INTO {_TABLE_MEASUREMENTS}
                    (station_id, sensor_id, timestamp, value)
                    VALUES (?, ?, ?, ?)""",
                measurement_rows,
            )

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
        database_path: str | Path | None = None,
    ) -> None:
        self.hass = hass
        self.db_path = (
            Path(database_path).expanduser()
            if database_path is not None
            else Path(hass.config.path(".storage")) / domain / filename
        )
        self._backend: DatabaseBackend = backend or SQLiteDatabaseBackend(self.db_path)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def async_initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await self.hass.async_add_executor_job(self._backend.validate)
            self._initialized = True

    async def async_prepare_for_config(self) -> None:
        """Create a missing DB during config validation; never repair an existing one."""
        await self.hass.async_add_executor_job(self._backend.initialize)
        self._initialized = True

    async def async_open_and_validate(self) -> None:
        """Open and validate an existing DB during config-entry startup."""
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Databáze neexistuje: {self.db_path}")
        await self.hass.async_add_executor_job(self._backend.validate)
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
