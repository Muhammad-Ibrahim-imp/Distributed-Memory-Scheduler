"""Unit coverage for Object Manager trust-state transitions."""

from common.types.security import TrustState
from dsm.cache import ObjectCache
from dsm.directory.directory import Directory, ObjectRecord, ObjectState
from dsm.object_manager import ObjectManager, ObjectManagerConfig
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def make_manager() -> tuple[ObjectManager, Directory]:
    """Build a manager with one recorded object."""
    directory = Directory()
    directory.put(
        ObjectRecord(
            object_id="obj-1",
            owner_identity="alice",
            size=8,
            node_id="node-1",
        )
    )
    manager = ObjectManager(
        directory,
        FakeNodeClient(),
        FakeScheduler(),
        FakeSecurity(),
        ObjectManagerConfig(),
    )
    return manager, directory


def test_revocation_handler_unknown_node_is_noop() -> None:
    """An event for a node without objects returns an empty id list."""
    manager, _ = make_manager()

    assert manager.handle_node_revoked("unknown", 1.0) == []


def test_revocation_handler_is_idempotent() -> None:
    """Repeated revocations keep objects unavailable at the first timestamp."""
    manager, directory = make_manager()

    assert manager.handle_node_revoked("node-1", 1.0) == ["obj-1"]
    assert manager.handle_node_revoked("node-1", 2.0) == ["obj-1"]
    assert directory.get("obj-1").state is ObjectState.UNAVAILABLE
    assert directory.get("obj-1").unavailable_since == 1.0


def test_suspicious_state_preserves_allocated_objects() -> None:
    """Suspicious nodes keep existing objects in their current allocated state."""
    manager, directory = make_manager()

    assert manager.handle_node_trust_state_changed(
        "node-1",
        TrustState.SUSPICIOUS,
        1.0,
    ) == ["obj-1"]
    assert directory.get("obj-1").state is ObjectState.ALLOCATED


def test_node_failure_marks_objects_unavailable_and_invalidates_cache() -> None:
    directory = Directory()
    directory.put(
        ObjectRecord(
            object_id="obj-1",
            owner_identity="alice",
            size=8,
            node_id="node-1",
        )
    )
    cache = ObjectCache()
    cache.put("obj-1", 0, b"data")
    manager = ObjectManager(
        directory,
        FakeNodeClient(),
        FakeScheduler(),
        FakeSecurity(),
        ObjectManagerConfig(),
        cache=cache,
    )

    assert manager.handle_node_failure("node-1") == ["obj-1"]
    assert directory.get("obj-1").state is ObjectState.UNAVAILABLE
    assert cache.get("obj-1") is None
