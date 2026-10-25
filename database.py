"""
SQLite persistence layer (async, via aiosqlite).

Tables:
  users    - one row per Telegram user, with first/last seen timestamps
  settings - per-user preferences (voice, language, speed)
  logs     - event log for usage analytics and debugging (no message content stored)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

log = logging.getLogger("voicebot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    user_id    INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    voice      TEXT NOT NULL,
    language   TEXT NOT NULL,
    speed      REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_user ON logs(user_id);
"""

# Allowlist of columns that update_setting may write. Guards the dynamic SQL below.
ALLOWED_SETTINGS = frozenset({"voice", "language", "speed"})


@dataclass(frozen=True)
class UserSettings:
    voice: str
    language: str
    speed: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin async wrapper around a single SQLite connection."""

    def __init__(self, path: str, defaults: UserSettings) -> None:
        self._path = path
        self._defaults = defaults
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        log.info("Database ready at %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        return self._conn

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
            """,
            (user_id, username, first_name, now, now),
        )
        await self.conn.commit()

    async def get_settings(self, user_id: int) -> UserSettings:
        async with self.conn.execute(
            "SELECT voice, language, speed FROM settings WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return self._defaults
        return UserSettings(voice=row["voice"], language=row["language"], speed=float(row["speed"]))

    async def update_setting(self, user_id: int, field: str, value: str | float) -> None:
        """Create the settings row if needed, then update a single allowed field."""
        if field not in ALLOWED_SETTINGS:
            raise ValueError(f"Unknown setting: {field}")

        now = _now()
        d = self._defaults
        await self.conn.execute(
            """
            INSERT INTO settings (user_id, voice, language, speed, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, d.voice, d.language, d.speed, now),
        )
        # `field` is validated against ALLOWED_SETTINGS, so this interpolation is safe.
        await self.conn.execute(
            f"UPDATE settings SET {field} = ?, updated_at = ? WHERE user_id = ?",
            (value, now, user_id),
        )
        await self.conn.commit()

    async def add_log(self, user_id: int | None, event: str, detail: str | None = None) -> None:
        """Record an event. Never pass user message content or secrets here."""
        await self.conn.execute(
            "INSERT INTO logs (user_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
            (user_id, event, (detail or "")[:200], _now()),
        )
        await self.conn.commit()
