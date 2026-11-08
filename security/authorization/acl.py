"""Authorization model: simple ownership ACL (SECURITY_POLICY.md section 8).

An object belongs to the identity that allocated it. Only that identity may
read, write or free it. There is no RBAC or sharing at Level 1.

Boundary (policy 8.3):
  * M5 (this file) defines what "unauthorized" means and audits every denial.
  * A2 stores owner_identity in object metadata and calls check_access()
    at the point the DSM operation executes.
  * B1 supplies the authenticated identity the check runs against.

Audit: pass `log_event=log_security_event` (security/monitoring/audit.py).
It is called with keyword arguments matching that function's signature. If the
sink raises, the exception propagates and the request fails: the check fails
CLOSED, never open.

A2 note: check_access() returns False for both an unknown object and a wrong
owner. A2 can call owner_of() first if it wants to raise ObjectNotFoundError
versus NotOwnerError.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Callable

from common.types.security import EventType, Result, Severity


class Action(str, Enum):
    READ = "read"
    WRITE = "write"
    FREE = "free"


AuditSink = Callable[..., None]


class AccessControl:
    def __init__(self, log_event: AuditSink | None = None):
        self._owners: dict[str, str] = {}
        self._lock = threading.Lock()
        self._log_event = log_event

    # -- ownership registry ---------------------------------------------------

    def register_object(self, object_id: str, owner_identity: str) -> None:
        """Record the owner when dsm_alloc creates an object."""
        if not object_id or not owner_identity:
            raise ValueError("object_id and owner_identity are required")
        with self._lock:
            current = self._owners.get(object_id)
            if current is not None and current != owner_identity:
                raise ValueError(f"object {object_id} is already owned by another identity")
            self._owners[object_id] = owner_identity

    def unregister_object(self, object_id: str) -> None:
        """Forget an object after dsm_free. Unknown ids are a silent no-op."""
        with self._lock:
            self._owners.pop(object_id, None)

    def owner_of(self, object_id: str) -> str | None:
        with self._lock:
            return self._owners.get(object_id)

    def objects_owned_by(self, identity: str) -> set[str]:
        with self._lock:
            return {oid for oid, owner in self._owners.items() if owner == identity}

    # -- the check ------------------------------------------------------------

    def check_access(
        self,
        identity: str,
        object_id: str,
        action: Action = Action.READ,
        request_id: str = "-",
    ) -> bool:
        """True only if `identity` owns `object_id`. Every denial is audited."""
        action = Action(action)
        owner = self.owner_of(object_id)

        if owner is None:
            reason = "unknown_object"
        elif not identity or owner != identity:
            reason = "not_owner"
        else:
            return True

        if self._log_event is not None:
            self._log_event(
                event_type=EventType.AUTHZ_DENIED,
                actor_id=identity or "unknown",
                severity=Severity.WARNING,
                result=Result.REJECTED,
                request_id=request_id,
                object_id=object_id,
                action=action.value,
                reason=reason,
            )
        return False