#!/usr/bin/env python3
"""SQLite-backed local history for MicMango transcripts."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = APP_DIR / "local-only" / "data" / "history.sqlite3"


class HistoryStore:
    def __init__(self, database: Path = DEFAULT_DATABASE):
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    text TEXT NOT NULL,
                    language TEXT NOT NULL,
                    character_count INTEGER NOT NULL,
                    transcription_seconds REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS transcripts_date_idx "
                "ON transcripts(local_date, id DESC)"
            )

    def add(self, text: str, language: str, transcription_seconds: float) -> int:
        now = datetime.now().astimezone()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transcripts(
                    created_at, local_date, text, language,
                    character_count, transcription_seconds
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now.isoformat(timespec="seconds"),
                    now.date().isoformat(),
                    text,
                    language,
                    len(text),
                    round(transcription_seconds, 3),
                ),
            )
            return int(cursor.lastrowid)

    def entries(self, local_date: str | None = None, limit: int = 200) -> list[dict[str, object]]:
        selected_date = local_date or date.today().isoformat()
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, local_date, text, language,
                       character_count, transcription_seconds
                FROM transcripts
                WHERE local_date = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (selected_date, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, days: int = 7) -> dict[str, object]:
        today = date.today().isoformat()
        with self._connect() as connection:
            today_row = connection.execute(
                """
                SELECT COUNT(*) AS entries, COALESCE(SUM(character_count), 0) AS characters
                FROM transcripts WHERE local_date = ?
                """,
                (today,),
            ).fetchone()
            recent_rows = connection.execute(
                """
                SELECT local_date, COUNT(*) AS entries,
                       COALESCE(SUM(character_count), 0) AS characters
                FROM transcripts
                GROUP BY local_date
                ORDER BY local_date DESC
                LIMIT ?
                """,
                (max(1, min(days, 31)),),
            ).fetchall()
        return {
            "date": today,
            "entries": int(today_row["entries"]),
            "characters": int(today_row["characters"]),
            "recent_days": [dict(row) for row in recent_rows],
        }
