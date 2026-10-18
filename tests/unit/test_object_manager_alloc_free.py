"""Allocation and free behavior for the DSM Object Manager."""

from uuid import UUID
from typing import NoReturn

import pytest

from common.types.context import RequestContext
from common.types.errors import (
    DSMTimeoutError,
    InvalidRequestError,
    NotOwnerError,
    OutOfCapacityError,
    QuotaExceededError,
)
from dsm.directory.directory import Directory, ObjectRecord, ObjectState
from dsm.object_manager import ObjectManager, ObjectManagerConfig
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def make_manager(
    node_ids: list[str] | None = None,
    max_alloc_attempts: int = 3,
) -> tuple[ObjectManager, Directory, FakeNodeClient, FakeScheduler, FakeSecurity]:
    """Build a manager and its observable fakes for one test."""
    directory = Directory()
    node_client = FakeNodeClient()
    scheduler = FakeScheduler(
        node_ids=(
            node_ids
            if node_ids is not None
            else ["node-1", "node-2", "node-3"]
        )
    )
    security = FakeSecurity()
    manager = ObjectManager(
        directory,
        node_client,
        scheduler,
        security,
        ObjectManagerConfig(MAX_ALLOC_ATTEMPTS=max_alloc_attempts),
    )
    return manager, directory, node_client, scheduler, security


def make_context(identity: str = "alice") -> RequestContext:
    """Create a request context for an operation."""
    return RequestContext(identity=identity, request_id="req-1")


async def test_alloc_stores_record_and_passes_scheduler_arguments() -> None:
    """Successful allocation stores the initial metadata after node success."""
    manager, directory, node, scheduler, _ = make_manager(["node-1"])

    object_id = await manager.alloc(make_context(), "alice", 64)

    record = directory.get(object_id)
    assert record == ObjectRecord(
        object_id=object_id,
        owner_identity="alice",
        size=64,
        node_id="node-1",
        version=0,
        state=ObjectState.ALLOCATED,
    )
    assert scheduler.calls == [{"object_id": object_id, "object_size": 64}]
    assert UUID(object_id).version == 4
    assert object_id in node.data


async def test_alloc_retries_on_node_capacity_and_uses_next_node() -> None:
    """An out-of-capacity node failure retries placement."""
    manager, directory, node, scheduler, _ = make_manager(["node-1", "node-2"])
    node.fail_next_alloc.append(OutOfCapacityError("node is full"))

    object_id = await manager.alloc(make_context(), "alice", 64)

    assert directory.get(object_id).node_id == "node-2"
    assert len(scheduler.calls) == 2
    assert [call[1][1] for call in node.call_log if call[0] == "alloc"] == [
        "node-1",
        "node-2",
    ]


async def test_alloc_raises_last_capacity_error_and_keeps_directory_unchanged() -> None:
    """Exhausted node capacity does not create metadata."""
    manager, directory, node, scheduler, _ = make_manager(
        ["node-1", "node-2", "node-3"]
    )
    node.fail_next_alloc.extend(
        [
            OutOfCapacityError("full-1"),
            OutOfCapacityError("full-2"),
            OutOfCapacityError("full-3"),
        ]
    )

    with pytest.raises(OutOfCapacityError, match="full-3"):
        await manager.alloc(make_context(), "alice", 64)

    assert all(directory.get(call["object_id"]) is None for call in scheduler.calls)
    assert len(scheduler.calls) == 3


async def test_alloc_does_not_retry_quota_exceeded() -> None:
    """Quota is client-wide and therefore fails without trying another node."""
    manager, _, node, scheduler, _ = make_manager(["node-1", "node-2"])
    node.fail_next_alloc.append(QuotaExceededError("quota reached"))

    with pytest.raises(QuotaExceededError, match="quota reached"):
        await manager.alloc(make_context(), "alice", 64)

    assert len(scheduler.calls) == 1
    assert len([call for call in node.call_log if call[0] == "alloc"]) == 1


async def test_alloc_maps_scheduler_runtime_and_value_errors() -> None:
    """Scheduler capacity and validation errors map to DSM-level errors."""
    manager, _, _, scheduler, _ = make_manager([])

    with pytest.raises(OutOfCapacityError, match="No eligible node"):
        await manager.alloc(make_context(), "alice", 64)
    assert len(scheduler.calls) == 1

    class InvalidScheduler:
        """Scheduler fake that rejects the request during placement."""

        def select_node_from_state(
            self,
            object_id: str,
            object_size: int,
        ) -> NoReturn:
            """Raise the invalid-placement error."""
            raise ValueError("invalid placement")

    manager.scheduler = InvalidScheduler()
    with pytest.raises(InvalidRequestError, match="invalid placement"):
        await manager.alloc(make_context(), "alice", 64)


async def test_alloc_propagates_unexpected_scheduler_errors_unchanged() -> None:
    """Scheduler failures outside the documented mappings remain untouched."""
    manager, _, _, _, _ = make_manager(["node-1"])
    expected_error = LookupError("scheduler backend failed")

    class FailingScheduler:
        """Scheduler fake that raises an unrelated backend error."""

        def select_node_from_state(
            self,
            object_id: str,
            object_size: int,
        ) -> NoReturn:
            """Raise the configured backend error instance."""
            raise expected_error

    manager.scheduler = FailingScheduler()
    with pytest.raises(LookupError) as raised:
        await manager.alloc(make_context(), "alice", 64)

    assert raised.value is expected_error


async def test_alloc_cleans_up_after_timeout_then_retries() -> None:
    """Timeout cleanup is best effort and the next placement can succeed."""
    manager, directory, node, scheduler, _ = make_manager(["node-1", "node-2"])
    node.fail_next_alloc.append(DSMTimeoutError("allocation timed out"))
    node.fail_next_free.append(RuntimeError("cleanup failed"))

    object_id = await manager.alloc(make_context(), "alice", 64)

    assert directory.get(object_id).node_id == "node-2"
    assert [call[0] for call in node.call_log] == ["alloc", "free", "alloc"]
    assert node.call_log[1][1][1] == "node-1"
    assert len(scheduler.calls) == 2


@pytest.mark.parametrize("size", [0, -1, "4", 1.5, True])
async def test_alloc_rejects_invalid_sizes(size: object) -> None:
    """Allocation requires a positive, non-boolean integer size."""
    manager, _, _, scheduler, _ = make_manager()

    with pytest.raises(InvalidRequestError):
        await manager.alloc(make_context(), "alice", size)  # type: ignore[arg-type]

    assert scheduler.calls == []


async def test_two_allocations_return_different_ids() -> None:
    """Each allocation gets an independent UUID."""
    manager, _, _, _, _ = make_manager(["node-1", "node-2"])

    first_id = await manager.alloc(make_context(), "alice", 64)
    second_id = await manager.alloc(make_context(), "alice", 64)

    assert first_id != second_id


async def test_free_removes_directory_record_and_node_object() -> None:
    """Free removes the remote allocation before removing its metadata."""
    manager, directory, node, _, _ = make_manager(["node-1"])
    object_id = await manager.alloc(make_context(), "alice", 64)

    await manager.free(make_context(), "alice", object_id)

    assert directory.get(object_id) is None
    assert object_id not in node.data


async def test_double_free_and_unknown_free_are_no_ops() -> None:
    """Free is idempotent for existing and unknown identifiers."""
    manager, directory, node, _, _ = make_manager(["node-1"])
    object_id = await manager.alloc(make_context(), "alice", 64)

    await manager.free(make_context(), "alice", object_id)
    call_count = len(node.call_log)
    await manager.free(make_context(), "alice", object_id)
    await manager.free(make_context(), "alice", "unknown")

    assert directory.get(object_id) is None
    assert len(node.call_log) == call_count


async def test_non_owner_cannot_free_object() -> None:
    """A denied owner check leaves both directory and node state untouched."""
    manager, directory, node, _, _ = make_manager(["node-1"])
    object_id = await manager.alloc(make_context(), "alice", 64)
    call_count = len(node.call_log)

    with pytest.raises(NotOwnerError):
        await manager.free(make_context("mallory"), "mallory", object_id)

    assert directory.get(object_id) is not None
    assert object_id in node.data
    assert len(node.call_log) == call_count


async def test_free_revoked_object_skips_node_call() -> None:
    """A revoked node is not contacted when its directory entry is removed."""
    manager, directory, node, _, _ = make_manager(["node-1"])
    object_id = await manager.alloc(make_context(), "alice", 64)
    directory.get(object_id).state = ObjectState.REVOKED
    call_count = len(node.call_log)

    await manager.free(make_context(), "alice", object_id)

    assert directory.get(object_id) is None
    assert len(node.call_log) == call_count


async def test_free_timeout_keeps_directory_record() -> None:
    """A timed-out node deletion retains metadata so the owner can retry."""
    manager, directory, node, _, _ = make_manager(["node-1"])
    object_id = await manager.alloc(make_context(), "alice", 64)
    node.fail_next_free.append(DSMTimeoutError("free timed out"))

    with pytest.raises(DSMTimeoutError, match="free timed out"):
        await manager.free(make_context(), "alice", object_id)

    assert directory.get(object_id) is not None
    assert object_id in node.data
