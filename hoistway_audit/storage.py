from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS audit_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_calls (
  call_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  input_bytes INTEGER NOT NULL,
  issued_at_ms INTEGER NOT NULL,
  mutating INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  output_digest TEXT NOT NULL,
  input_bytes INTEGER NOT NULL,
  output_bytes INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  observed_at_ms INTEGER NOT NULL,
  mutating INTEGER NOT NULL,
  UNIQUE(call_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_observation_fingerprint
  ON observations(tool_name, input_digest);
CREATE INDEX IF NOT EXISTS idx_observation_time
  ON observations(observed_at_ms);
"""


@dataclass(frozen=True)
class PendingCall:
    call_id: str
    session_id: str
    tool_name: str
    input_digest: str
    input_bytes: int
    issued_at_ms: int
    mutating: bool


@dataclass(frozen=True)
class Observation:
    session_id: str
    tool_name: str
    input_digest: str
    output_digest: str
    latency_ms: int
    observed_at_ms: int
    mutating: bool


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO audit_meta(key, value) VALUES('started_at_ms', ?)",
                (str(int(time.time() * 1000)),),
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def save_pending(self, calls: Iterable[PendingCall]) -> None:
        with self._lock, self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO pending_calls(
                  call_id, session_id, tool_name, input_digest, input_bytes, issued_at_ms, mutating
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                  session_id=excluded.session_id,
                  tool_name=excluded.tool_name,
                  input_digest=excluded.input_digest,
                  input_bytes=excluded.input_bytes,
                  issued_at_ms=excluded.issued_at_ms,
                  mutating=excluded.mutating
                """,
                [
                    (
                        call.call_id,
                        call.session_id,
                        call.tool_name,
                        call.input_digest,
                        call.input_bytes,
                        call.issued_at_ms,
                        int(call.mutating),
                    )
                    for call in calls
                ],
            )

    def complete_call(
        self,
        call_id: str,
        session_id: str,
        output_digest: str,
        output_bytes: int,
        completed_at_ms: int,
    ) -> bool:
        with self._lock, self.connect() as connection:
            pending = connection.execute(
                "SELECT * FROM pending_calls WHERE call_id = ?", (call_id,)
            ).fetchone()
            if not pending:
                return False
            connection.execute(
                """
                INSERT OR IGNORE INTO observations(
                  call_id, session_id, tool_name, input_digest, output_digest,
                  input_bytes, output_bytes, latency_ms, observed_at_ms, mutating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    session_id or pending["session_id"],
                    pending["tool_name"],
                    pending["input_digest"],
                    output_digest,
                    pending["input_bytes"],
                    output_bytes,
                    max(0, completed_at_ms - pending["issued_at_ms"]),
                    completed_at_ms,
                    pending["mutating"],
                ),
            )
            connection.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))
            return True

    def observations(self) -> list[Observation]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, tool_name, input_digest, output_digest,
                       latency_ms, observed_at_ms, mutating
                FROM observations ORDER BY observed_at_ms
                """
            ).fetchall()
        return [
            Observation(
                session_id=row["session_id"],
                tool_name=row["tool_name"],
                input_digest=row["input_digest"],
                output_digest=row["output_digest"],
                latency_ms=row["latency_ms"],
                observed_at_ms=row["observed_at_ms"],
                mutating=bool(row["mutating"]),
            )
            for row in rows
        ]

    def add_observation(
        self,
        call_id: str,
        session_id: str,
        tool_name: str,
        input_digest: str,
        output_digest: str,
        latency_ms: int,
        observed_at_ms: int,
        mutating: bool = False,
        input_bytes: int = 0,
        output_bytes: int = 0,
    ) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO observations(
                  call_id, session_id, tool_name, input_digest, output_digest,
                  input_bytes, output_bytes, latency_ms, observed_at_ms, mutating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    session_id,
                    tool_name,
                    input_digest,
                    output_digest,
                    input_bytes,
                    output_bytes,
                    max(0, latency_ms),
                    observed_at_ms,
                    int(mutating),
                ),
            )

    def started_at_ms(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM audit_meta WHERE key='started_at_ms'"
            ).fetchone()
        return int(row["value"])

    def reset(self, started_at_ms: int | None = None) -> None:
        effective_start = int(time.time() * 1000) if started_at_ms is None else started_at_ms
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM pending_calls")
            connection.execute("DELETE FROM observations")
            connection.execute(
                "UPDATE audit_meta SET value=? WHERE key='started_at_ms'",
                (str(effective_start),),
            )
