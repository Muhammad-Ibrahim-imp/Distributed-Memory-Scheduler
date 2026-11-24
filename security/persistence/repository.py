"""Parameterized persistence operations for M5-owned security records."""
from __future__ import annotations

import json

from common.types.security import SecurityEvent, TrustState
from security.authentication.registration import Identity, Role
from security.persistence.database import SecurityDatabase


class SecurityRepository:
    def __init__(self, database: SecurityDatabase):
        self.database = database

    def load_identities(self) -> list[Identity]:
        rows = self.database.run(lambda db: db.execute(
            "SELECT identity,label,role,credential_hash,created_at,revoked FROM identities"
        ).fetchall())
        return [Identity(row[0], row[1], Role(row[2]), row[3], row[4], bool(row[5])) for row in rows]

    def save_identity(self, identity: Identity) -> None:
        self.database.run(lambda db: db.execute(
            "INSERT INTO identities(identity,label,role,credential_hash,created_at,revoked) "
            "VALUES(?,?,?,?,?,?)",
            (identity.identity, identity.label, identity.role.value, identity.credential_hash,
             identity.created_at, int(identity.revoked)),
        ))

    def set_identity_revoked(self, identity: str) -> bool:
        def write(db):
            db.execute("UPDATE identities SET revoked=1 WHERE identity=?", (identity,))
            return db.execute("SELECT 1 FROM identities WHERE identity=?", (identity,)).fetchone() is not None
        return self.database.run(write)

    def load_trust(self) -> list[tuple[str, TrustState, float, str]]:
        rows = self.database.run(lambda db: db.execute(
            "SELECT identity,state,updated_at,reason FROM trust_states"
        ).fetchall())
        return [(i, TrustState(s), t, r) for i, s, t, r in rows]

    def save_trust(self, identity: str, state: TrustState, timestamp: float, reason: str) -> None:
        self.database.run(lambda db: db.execute(
            "INSERT INTO trust_states(identity,state,updated_at,reason) VALUES(?,?,?,?) "
            "ON CONFLICT(identity) DO UPDATE SET state=excluded.state, "
            "updated_at=excluded.updated_at, reason=excluded.reason",
            (identity, state.value, timestamp, reason),
        ))

    def save_trust_transition(self, identity: str, state: TrustState, timestamp: float,
                              reason: str, event: SecurityEvent) -> None:
        encoded = json.dumps(event.to_dict(), sort_keys=True, default=str)
        def write(db):
            db.execute(
                "INSERT INTO trust_states(identity,state,updated_at,reason) VALUES(?,?,?,?) "
                "ON CONFLICT(identity) DO UPDATE SET state=excluded.state, "
                "updated_at=excluded.updated_at, reason=excluded.reason",
                (identity, state.value, timestamp, reason),
            )
            db.execute(
                "INSERT INTO audit_events(event_id,occurred_at,event_type,actor_id,result,request_id,event_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (event.event_id, event.timestamp, event.event_type.value, event.actor_id,
                 event.result.value, event.request_id, encoded),
            )
        self.database.run(write)

    def load_violations(self, window_start: float) -> list[tuple[str, str, float]]:
        return self.database.run(lambda db: db.execute(
            "SELECT identity,event_type,occurred_at FROM trust_violations "
            "WHERE occurred_at>=? OR (identity,occurred_at) IN "
            "(SELECT identity,MAX(occurred_at) FROM trust_violations GROUP BY identity) "
            "ORDER BY occurred_at", (window_start,)
        ).fetchall())

    def add_violation(self, identity: str, event_type: str, timestamp: float) -> None:
        self.database.run(lambda db: db.execute(
            "INSERT INTO trust_violations(identity,event_type,occurred_at) VALUES(?,?,?)",
            (identity, event_type, timestamp),
        ))

    def prune_violations(self, identity: str, cutoff: float) -> None:
        self.database.run(lambda db: db.execute(
            "DELETE FROM trust_violations WHERE identity=? AND occurred_at<?", (identity, cutoff)
        ))

    def add_ban(self, value: str, kind: str, timestamp: float, reason: str,
                identity: str | None = None) -> None:
        self.database.run(lambda db: db.execute(
            "INSERT OR IGNORE INTO bans(value,kind,identity,created_at,reason) VALUES(?,?,?,?,?)",
            (value, kind, identity, timestamp, reason),
        ))

    def has_ban(self, value: str, kind: str) -> bool:
        return self.database.run(lambda db: db.execute(
            "SELECT 1 FROM bans WHERE value=? AND kind=?", (value, kind)
        ).fetchone() is not None)

    def add_audit_event(self, event: SecurityEvent) -> None:
        encoded = json.dumps(event.to_dict(), sort_keys=True, default=str)
        self.database.run(lambda db: db.execute(
            "INSERT OR IGNORE INTO audit_events(event_id,occurred_at,event_type,actor_id,result,request_id,event_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (event.event_id, event.timestamp, event.event_type.value, event.actor_id,
             event.result.value, event.request_id, encoded),
        ))

    def close(self) -> None:
        self.database.close()
