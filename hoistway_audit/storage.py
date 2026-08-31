from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS audit_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_calls (
  call_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL DEFAULT '',
  batch_size INTEGER NOT NULL DEFAULT 1,
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
  batch_id TEXT NOT NULL DEFAULT '',
  batch_size INTEGER NOT NULL DEFAULT 1,
  session_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  input_digest TEXT NOT NULL,
  output_digest TEXT NOT NULL,
  output_simhash INTEGER,
  output_url_set_digest TEXT,
  input_bytes INTEGER NOT NULL,
  output_bytes INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  observed_at_ms INTEGER NOT NULL,
  mutating INTEGER NOT NULL,
  UNIQUE(call_id, session_id)
);
CREATE TABLE IF NOT EXISTS repeat_pairs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id TEXT NOT NULL UNIQUE,
  prev_call_id TEXT NOT NULL,
  call_digest TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  prev_session_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  prev_ts INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  prev_output_digest TEXT NOT NULL,
  output_digest TEXT NOT NULL,
  prev_output_simhash INTEGER,
  output_simhash INTEGER,
  prev_url_set_digest TEXT,
  output_url_set_digest TEXT
);
CREATE INDEX IF NOT EXISTS idx_observation_fingerprint
  ON observations(tool_name, input_digest);
CREATE INDEX IF NOT EXISTS idx_observation_time
  ON observations(observed_at_ms);
CREATE INDEX IF NOT EXISTS idx_repeat_pair_tool_gap
  ON repeat_pairs(tool_name, prev_ts, ts);
"""


@dataclass(frozen=True)
class PendingCall:
    call_id: str
    batch_id: str
    batch_size: int
    session_id: str
    tool_name: str
    input_digest: str
    input_bytes: int
    issued_at_ms: int
    mutating: bool


@dataclass(frozen=True)
class Observation:
    call_id: str
    batch_id: str
    batch_size: int
    session_id: str
    tool_name: str
    input_digest: str
    output_digest: str
    output_simhash: int | None
    output_url_set_digest: str | None
    latency_ms: int
    observed_at_ms: int
    mutating: bool


@dataclass(frozen=True)
class RepeatPair:
    call_id: str
    prev_call_id: str
    call_digest: str
    tool_name: str
    prev_session_id: str
    session_id: str
    prev_ts: int
    ts: int
    prev_output_digest: str
    output_digest: str
    prev_output_simhash: int | None
    output_simhash: int | None
    prev_url_set_digest: str | None
    output_url_set_digest: str | None


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
            connection.execute(
                "INSERT OR IGNORE INTO audit_meta(key, value) VALUES('started_at_ms', ?)",
                (str(int(time.time() * 1000)),),
            )

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        for table in ("pending_calls", "observations"):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "batch_id" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            if "batch_size" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN batch_size INTEGER NOT NULL DEFAULT 1")
        observation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(observations)")}
        if "output_simhash" not in observation_columns:
            connection.execute("ALTER TABLE observations ADD COLUMN output_simhash INTEGER")
        if "output_url_set_digest" not in observation_columns:
            connection.execute("ALTER TABLE observations ADD COLUMN output_url_set_digest TEXT")
        pair_columns = {row["name"] for row in connection.execute("PRAGMA table_info(repeat_pairs)")}
        for name, sql_type in (
            ("prev_output_simhash", "INTEGER"),
            ("output_simhash", "INTEGER"),
            ("prev_url_set_digest", "TEXT"),
            ("output_url_set_digest", "TEXT"),
        ):
            if name not in pair_columns:
                connection.execute(f"ALTER TABLE repeat_pairs ADD COLUMN {name} {sql_type}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_pending(self, calls: Iterable[PendingCall]) -> None:
        with self._lock, self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO pending_calls(
                  call_id, batch_id, batch_size, session_id, tool_name, input_digest,
                  input_bytes, issued_at_ms, mutating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                  session_id=excluded.session_id,
                  batch_id=excluded.batch_id,
                  batch_size=excluded.batch_size,
                  tool_name=excluded.tool_name,
                  input_digest=excluded.input_digest,
                  input_bytes=excluded.input_bytes,
                  issued_at_ms=excluded.issued_at_ms,
                  mutating=excluded.mutating
                """,
                [
                    (
                        call.call_id,
                        call.batch_id,
                        call.batch_size,
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
        output_simhash: int | None,
        output_url_set_digest: str | None,
        output_bytes: int,
        completed_at_ms: int,
    ) -> bool:
        with self._lock, self.connect() as connection:
            pending = connection.execute(
                "SELECT * FROM pending_calls WHERE call_id = ?", (call_id,)
            ).fetchone()
            if not pending:
                return False
            previous = connection.execute(
                """
                SELECT call_id, session_id, observed_at_ms, output_digest,
                       output_simhash, output_url_set_digest
                FROM observations
                WHERE tool_name=? AND input_digest=?
                ORDER BY observed_at_ms DESC, id DESC LIMIT 1
                """,
                (pending["tool_name"], pending["input_digest"]),
            ).fetchone()
            effective_session = session_id or pending["session_id"]
            connection.execute(
                """
                INSERT OR IGNORE INTO observations(
                  call_id, batch_id, batch_size, session_id, tool_name, input_digest,
                  output_digest, output_simhash, output_url_set_digest, input_bytes,
                  output_bytes, latency_ms, observed_at_ms, mutating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    pending["batch_id"] or call_id,
                    pending["batch_size"],
                    effective_session,
                    pending["tool_name"],
                    pending["input_digest"],
                    output_digest,
                    output_simhash,
                    output_url_set_digest,
                    pending["input_bytes"],
                    output_bytes,
                    max(0, completed_at_ms - pending["issued_at_ms"]),
                    completed_at_ms,
                    pending["mutating"],
                ),
            )
            if previous:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO repeat_pairs(
                      call_id, prev_call_id, call_digest, tool_name, prev_session_id,
                      session_id, prev_ts, ts, prev_output_digest, output_digest,
                      prev_output_simhash, output_simhash, prev_url_set_digest, output_url_set_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        call_id,
                        previous["call_id"],
                        pending["input_digest"],
                        pending["tool_name"],
                        previous["session_id"],
                        effective_session,
                        previous["observed_at_ms"],
                        completed_at_ms,
                        previous["output_digest"],
                        output_digest,
                        previous["output_simhash"],
                        output_simhash,
                        previous["output_url_set_digest"],
                        output_url_set_digest,
                    ),
                )
            connection.execute("DELETE FROM pending_calls WHERE call_id = ?", (call_id,))
            return True

    def observations(self) -> list[Observation]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT call_id, batch_id, batch_size, session_id, tool_name, input_digest, output_digest,
                       output_simhash, output_url_set_digest,
                       latency_ms, observed_at_ms, mutating
                FROM observations ORDER BY observed_at_ms
                """
            ).fetchall()
        return [
            Observation(
                call_id=row["call_id"],
                batch_id=row["batch_id"] or row["call_id"],
                batch_size=row["batch_size"],
                session_id=row["session_id"],
                tool_name=row["tool_name"],
                input_digest=row["input_digest"],
                output_digest=row["output_digest"],
                output_simhash=row["output_simhash"],
                output_url_set_digest=row["output_url_set_digest"],
                latency_ms=row["latency_ms"],
                observed_at_ms=row["observed_at_ms"],
                mutating=bool(row["mutating"]),
            )
            for row in rows
        ]

    def repeat_pairs(self) -> list[RepeatPair]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT call_id, prev_call_id, call_digest, tool_name, prev_session_id,
                       session_id, prev_ts, ts, prev_output_digest, output_digest
                       , prev_output_simhash, output_simhash, prev_url_set_digest, output_url_set_digest
                FROM repeat_pairs ORDER BY ts, id
                """
            ).fetchall()
        return [RepeatPair(**dict(row)) for row in rows]

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
        output_simhash: int | None = None,
        output_url_set_digest: str | None = None,
        batch_id: str | None = None,
        batch_size: int = 1,
    ) -> None:
        with self._lock, self.connect() as connection:
            previous = connection.execute(
                """
                SELECT call_id, session_id, observed_at_ms, output_digest,
                       output_simhash, output_url_set_digest
                FROM observations
                WHERE tool_name=? AND input_digest=? AND call_id<>?
                ORDER BY observed_at_ms DESC, id DESC LIMIT 1
                """,
                (tool_name, input_digest, call_id),
            ).fetchone()
            connection.execute(
                """
                INSERT OR REPLACE INTO observations(
                  call_id, batch_id, batch_size, session_id, tool_name, input_digest,
                  output_digest, output_simhash, output_url_set_digest, input_bytes,
                  output_bytes, latency_ms, observed_at_ms, mutating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    batch_id or call_id,
                    batch_size,
                    session_id,
                    tool_name,
                    input_digest,
                    output_digest,
                    output_simhash,
                    output_url_set_digest,
                    input_bytes,
                    output_bytes,
                    max(0, latency_ms),
                    observed_at_ms,
                    int(mutating),
                ),
            )
            if previous:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO repeat_pairs(
                      call_id, prev_call_id, call_digest, tool_name, prev_session_id,
                      session_id, prev_ts, ts, prev_output_digest, output_digest,
                      prev_output_simhash, output_simhash, prev_url_set_digest, output_url_set_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        call_id,
                        previous["call_id"],
                        input_digest,
                        tool_name,
                        previous["session_id"],
                        session_id,
                        previous["observed_at_ms"],
                        observed_at_ms,
                        previous["output_digest"],
                        output_digest,
                        previous["output_simhash"],
                        output_simhash,
                        previous["output_url_set_digest"],
                        output_url_set_digest,
                    ),
                )

    def started_at_ms(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM audit_meta WHERE key='started_at_ms'"
            ).fetchone()
        return int(row["value"])

    def increment_dropped_missing_session(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_meta(key, value) VALUES('dropped_missing_session', ?)
                ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER) + excluded.value
                """,
                (str(count),),
            )

    def dropped_missing_session(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM audit_meta WHERE key='dropped_missing_session'"
            ).fetchone()
        return int(row["value"]) if row else 0

    def reset(self, started_at_ms: int | None = None) -> None:
        effective_start = int(time.time() * 1000) if started_at_ms is None else started_at_ms
        with self._lock, self.connect() as connection:
            connection.execute("DELETE FROM pending_calls")
            connection.execute("DELETE FROM observations")
            connection.execute("DELETE FROM repeat_pairs")
            connection.execute("DELETE FROM audit_meta WHERE key='dropped_missing_session'")
            connection.execute(
                "UPDATE audit_meta SET value=? WHERE key='started_at_ms'",
                (str(effective_start),),
            )
