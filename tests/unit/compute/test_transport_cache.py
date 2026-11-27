import pytest

from compute.transport import CoordinatorDSMProxy


class _Memory:
    def __init__(self):
        self.calls = 0

    async def read_sealed(self, *_args):
        self.calls += 1
        return b"tile"


class _Service:
    def __init__(self):
        self.memory_node = _Memory()
        self.identity = "worker"
        self.token = "token"
        self.coordinator_generation = "gen-1"
        self.connection = object()
        self.tile_cache_limit = 8
        self.tile_cache = __import__("collections").OrderedDict()
        self.tile_cache_bytes = 0
        self.tile_cache_hits = 0
        self.tile_cache_misses = 0


@pytest.mark.asyncio
async def test_direct_immutable_tile_cache_is_bounded_and_task_scoped():
    service = _Service()
    versions = {"obj": 7}
    first = CoordinatorDSMProxy(service, "task-a", "owner", ("obj",), versions)
    assert await first.read("obj") == b"tile"
    second = CoordinatorDSMProxy(service, "task-b", "owner", ("obj",), versions)
    assert await second.read("obj") == b"tile"
    assert service.memory_node.calls == 1
    assert second.metrics["cache_hit_count"] == 1
    assert second.metrics["cache_bytes_avoided"] == 4

    unauthorized = CoordinatorDSMProxy(service, "task-c", "owner", (), versions)
    with pytest.raises(PermissionError):
        await unauthorized.read("obj")

    newer = CoordinatorDSMProxy(service, "task-d", "owner", ("obj",), {"obj": 8})
    assert await newer.read("obj") == b"tile"
    assert service.memory_node.calls == 2
    assert service.tile_cache_bytes <= service.tile_cache_limit
