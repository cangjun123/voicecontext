from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config_store import APP_DIR


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or APP_DIR / "history.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at_ms INTEGER NOT NULL,
                    ended_at_ms INTEGER,
                    transcript TEXT NOT NULL DEFAULT '',
                    final_prompt TEXT NOT NULL DEFAULT '',
                    source_app TEXT NOT NULL DEFAULT '',
                    reference_count INTEGER NOT NULL DEFAULT 0,
                    data_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS sessions_created_idx ON sessions(created_at_ms DESC)"
            )

    def save(self, session_data: dict[str, Any]) -> None:
        payload = json.dumps(session_data, ensure_ascii=False)
        reference_count = sum(1 for item in session_data.get("events", []) if item.get("type") == "reference")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions
                    (id, created_at_ms, ended_at_ms, transcript, final_prompt, source_app, reference_count, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    ended_at_ms=excluded.ended_at_ms,
                    transcript=excluded.transcript,
                    final_prompt=excluded.final_prompt,
                    source_app=excluded.source_app,
                    reference_count=excluded.reference_count,
                    data_json=excluded.data_json
                """,
                (
                    session_data["id"],
                    session_data.get("createdAtMs", int(time.time() * 1000)),
                    session_data.get("endedAtMs"),
                    session_data.get("transcript", ""),
                    session_data.get("finalPrompt", ""),
                    session_data.get("sourceApp", ""),
                    reference_count,
                    payload,
                ),
            )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at_ms, ended_at_ms, transcript, final_prompt, source_app, reference_count
                FROM sessions ORDER BY created_at_ms DESC LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT data_json FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return json.loads(row["data_json"]) if row else None

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def cleanup(self, retention_days: int) -> int:
        cutoff = int((time.time() - max(0, retention_days) * 86400) * 1000)
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE created_at_ms < ?", (cutoff,))
            return cursor.rowcount

    def clear(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM sessions")
