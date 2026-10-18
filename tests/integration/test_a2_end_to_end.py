"""End-to-end checks for A2's public runtime using in-memory fakes."""

from uuid import UUID

import pytest

from common.types.errors import (
    NotOwnerError,
    ObjectNotFoundError,
    ObjectUnavailableError,
)
from common.types.security import TrustState
from dsm.directory.directory import Directory, ObjectState
from dsm.object_manager import ObjectManagerConfig
from dsm.runtime import Runtime
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def make_runtime(
    node_ids: list[str] | None = None,
) -> tuple[Runtime, Directory, FakeSecurity, FakeNodeClient]:
    """Construct a runtime with node, scheduler, and security fakes."""
    directory = Directory()
    node_client = FakeNodeClient()
    security = FakeSecurity()
    runtime = Runtime(
        directory=directory,
        node_client=node_client,
        scheduler=FakeScheduler(
            node_ids=node_ids if node_ids is not None else ["node-1"] * 10
        ),
        security=security,
        config=ObjectManagerConfig(),
    )
    return runtime, directory, security, node_client


async def test_bound_handle_alloc_write_read_free_and_idempotence() -> None:
    """The frozen API works end to end without exposing identity parameters."""
    runtime, _, security, _ = make_runtime()
    handle = runtime.bind("alice")

    object_id = await handle.alloc(16)
    assert UUID(object_id).version == 4
    assert await handle.write(object_id, b"hello") is None
    assert await handle.read(object_id) == b"hello"
    await handle.free(object_id)
    await handle.free(object_id)

    with pytest.raises(ObjectNotFoundError):
        await handle.read(object_id)
    assert len(security.revocation_listeners) == 1


async def test_handles_cannot_read_write_or_free_another_identity_object() -> None:
    """The bound caller identity is used for each Object Manager operation."""
    runtime, _, _, _ = make_runtime()
    alice = runtime.bind("alice")
    bob = runtime.bind("bob")
    object_id = await alice.alloc(16)
    await alice.write(object_id, b"owned")

    with pytest.raises(NotOwnerError):
        await bob.read(object_id)
    with pytest.raises(NotOwnerError):
        await bob.write(object_id, b"changed")
    with pytest.raises(NotOwnerError):
        await bob.free(object_id)

    assert await alice.read(object_id) == b"owned"


async def test_revoked_objects_are_blocked_and_free_remains_available() -> None:
    """Revocation affects one node and an owner can still release its object."""
    runtime, directory, security, _ = make_runtime(["node-A", "node-B"])
    alice = runtime.bind("alice")
    node_a_id = await alice.alloc(16)
    node_b_id = await alice.alloc(16)
    await alice.write(node_b_id, b"other node")

    assert security.revoke_node("node-A", 42.0) == [[node_a_id]]
    assert directory.get(node_a_id).state is ObjectState.REVOKED
    assert directory.get(node_b_id).state is ObjectState.ALLOCATED
    with pytest.raises(ObjectUnavailableError):
        await alice.read(node_a_id)
    with pytest.raises(ObjectUnavailableError):
        await alice.write(node_a_id, b"blocked")

    await alice.free(node_a_id)
    assert directory.get(node_a_id) is None
    assert await alice.read(node_b_id) == b"other node"


async def test_quarantine_recovers_but_revocation_is_permanent() -> None:
    """Trust recovery restores quarantine only and never restores revocation."""
    runtime, directory, _, _ = make_runtime(["node-Q", "node-R"])
    alice = runtime.bind("alice")
    quarantined_id = await alice.alloc(16)
    revoked_id = await alice.alloc(16)
    manager = runtime.object_manager

    manager.handle_node_trust_state_changed("node-Q", TrustState.QUARANTINED, 1.0)
    assert await alice.read(quarantined_id) == b""
    with pytest.raises(ObjectUnavailableError):
        await alice.write(quarantined_id, b"blocked")
    manager.handle_node_trust_state_changed("node-Q", TrustState.TRUSTED, 2.0)
    await alice.write(quarantined_id, b"restored")
    assert await alice.read(quarantined_id) == b"restored"

    manager.handle_node_revoked("node-R", 3.0)
    manager.handle_node_trust_state_changed("node-R", TrustState.TRUSTED, 4.0)
    assert directory.get(revoked_id).state is ObjectState.REVOKED
