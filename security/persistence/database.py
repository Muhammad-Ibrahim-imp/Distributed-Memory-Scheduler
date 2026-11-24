"""Small, process-local SQLite database for coordinator security state."""
from __future__ import annotations

import sqlite3
import threading
import logging
from pathlib import Path


class SecurityPersistenceError(RuntimeError):
    """Durable security state could not be opened or updated safely."""


SCHEMA_VERSION = 2
_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identities (
  identity TEXT PRIMARY KEY, label TEXT NOT NULL, role TEXT NOT NULL,
  credential_hash TEXT NOT NULL, created_at REAL NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);
CREATE INDEX IF NOT EXISTS identities_role_idx ON identities(role);
CREATE TABLE IF NOT EXISTS trust_states (
  identity TEXT PRIMARY KEY REFERENCES identities(identity),
  state TEXT NOT NULL, updated_at REAL NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trust_violations (
  id INTEGER PRIMARY KEY, identity TEXT NOT NULL REFERENCES identities(identity),
  event_type TEXT NOT NULL, occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS trust_violations_window_idx
  ON trust_violations(identity, occurred_at);
CREATE TABLE IF NOT EXISTS bans (
  value TEXT PRIMARY KEY, kind TEXT NOT NULL, identity TEXT,
  created_at REAL NOT NULL, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bans_identity_idx ON bans(identity);
CREATE TABLE IF NOT EXISTS node_certificates (
  identity TEXT PRIMARY KEY REFERENCES identities(identity),
  fingerprint TEXT NOT NULL UNIQUE,
  enrolled_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  event_id TEXT PRIMARY KEY, occurred_at REAL NOT NULL, event_type TEXT NOT NULL,
  actor_id TEXT NOT NULL, result TEXT NOT NULL, request_id TEXT NOT NULL,
  event_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_actor_time_idx ON audit_events(actor_id, occurred_at);
"""


class SecurityDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        marker = self.path.parent.parent / f".{self.path.parent.name}.{self.path.name}.initialized"
        existed = self.path.exists()
        if not existed and marker.exists():
            raise SecurityPersistenceError(f"security database is missing: {self.path}")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, timeout=0, check_same_thread=False)
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA busy_timeout=0")
            with self._connection:
                self._connection.executescript(_SCHEMA)
                row = self._connection.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        "INSERT INTO metadata(key,value) VALUES('schema_version',?)",
                        (str(SCHEMA_VERSION),),
                    )
                elif int(row[0]) == 1:
                    self._connection.execute(
                        "UPDATE metadata SET value=? WHERE key='schema_version'",
                        (str(SCHEMA_VERSION),),
                    )
                elif int(row[0]) != SCHEMA_VERSION:
                    raise SecurityPersistenceError(f"unsupported security schema version: {row[0]}")
                check = self._connection.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    raise SecurityPersistenceError("security database integrity check failed")
            marker.write_text(str(SCHEMA_VERSION), encoding="ascii")
            try:
                self.path.chmod(0o600)
                marker.chmod(0o600)
            except OSError:
                pass  # Windows ACLs govern access on Windows.
        except SecurityPersistenceError:
            self.close()
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            logging.getLogger(__name__).exception("security database initialization failed")
            self.close()
            raise SecurityPersistenceError("could not initialize security database") from exc

    def run(self, operation):
        """Run one short operation under a transaction; fail closed on errors."""
        with self._lock:
            if self._connection is None:
                raise SecurityPersistenceError("security database is closed")
            try:
                with self._connection:
                    return operation(self._connection)
            except sqlite3.Error as exc:
                logging.getLogger(__name__).exception("security database operation failed")
                raise SecurityPersistenceError("security database operation failed") from exc

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None
