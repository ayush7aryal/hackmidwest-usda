"""Small SQLite helpers; each request owns its connection."""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema.sql"


def database_path() -> str:
    """Use a persistent /data path in Docker and a local file in development."""
    return os.environ.get("DATABASE_PATH", "data/facility.db")


def utc_now() -> str:
    """Persist timezone-aware UTC timestamps consistently."""
    return datetime.now(UTC).isoformat()


def connect(path: str) -> sqlite3.Connection:
    """Enable referential integrity on every connection, including tests."""
    # FastAPI may resume a sync dependency on a different thread in its pool.
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: str) -> None:
    """Apply the checked-in schema without replacing existing records."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        connection.executescript(SCHEMA_PATH.read_text())
    finally:
        connection.close()
