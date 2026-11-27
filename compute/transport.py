"""Task-scoped DSM proxy used by workers connected to a coordinator."""

from __future__ import annotations

import time
import asyncio
from uuid import uuid4

from common.protocol import dsm_pb2
from common.types.errors import RateLimitedError, TokenExpiredError
from network.tcp.errors import code_to_error


class CoordinatorDSMProxy:
    """Routes worker object access through coordinator authorization checks."""

    def __init__(self, service, task_id: str, owner_identity: str,
                 input_object_ids: tuple[str, ...], local_input_versions: dict[str, int] | None = None):
        self.service = service
        self.task_id = task_id
        self.owner_identity = owner_identity
        self.input_object_ids = frozenset(input_object_ids)
        self.local_input_versions = dict(local_input_versions or {})
        self.metrics = {"dsm_requests": 0, "remote_read_count": 0,
                        "remote_read_bytes": 0, "dsm_input_payload_bytes": 0,
                        "local_read_count": 0, "local_read_bytes": 0,
                        "dsm_output_payload_bytes": 0,
                        "local_output_write_count": 0, "local_output_write_bytes": 0,
                        "remote_output_write_count": 0, "remote_output_write_bytes": 0,
                        "seal_operation_count": 0}

    async def _call(self, operation: str, object_id: str = "", size: int = 0, data: bytes = b""):
        if self.service.connection is None or not self.service.identity or not self.service.token:
            raise ConnectionError("worker has no authenticated coordinator connection")
        for attempt in range(31):
            token = self.service.token
            env = dsm_pb2.Envelope()
            env.protocol_version = 1
            env.identity = self.service.identity
            env.token = token
            env.nonce = f"compute-{uuid4()}"
            env.timestamp = time.time()
            env.coordinator_generation = self.service.coordinator_generation
            request = env.compute_object_access
            request.request_id = f"compute-object-{uuid4()}"
            request.task_id = self.task_id
            request.operation = operation
            request.object_id = object_id
            request.size = size
            request.data = data
            response = await self.service.connection.call(env)
            if response.WhichOneof("payload") != "error":
                self.metrics["dsm_requests"] += 1
                if operation == "read":
                    self.metrics["remote_read_count"] += 1
                    self.metrics["remote_read_bytes"] += len(response.compute_object_response.data)
                    self.metrics["dsm_input_payload_bytes"] += len(response.compute_object_response.data)
                elif operation == "write":
                    self.metrics["dsm_output_payload_bytes"] += len(data)
                    target = ("local" if response.compute_object_response.storage_node_id
                              == self.service.identity else "remote")
                    self.metrics[f"{target}_output_write_count"] += 1
                    self.metrics[f"{target}_output_write_bytes"] += len(data)
                return response.compute_object_response
            error = code_to_error(response.error.code, response.error.message)
            if isinstance(error, TokenExpiredError):
                async with self.service._refresh_lock:
                    if self.service.token == token:
                        await self.service._refresh(self.service.connection)
            elif isinstance(error, RateLimitedError) and attempt < 30:
                await asyncio.sleep(0.2)
            else:
                raise error
        raise RateLimitedError("worker DSM access rate limit retry budget exhausted")

    async def alloc(self, size: int) -> str:
        return (await self._call("alloc", size=size)).object_id

    async def read(self, object_id: str) -> bytes:
        if object_id in self.local_input_versions:
            if object_id not in self.input_object_ids:
                raise PermissionError("task is not authorized for this local input")
            generation = self.service.coordinator_generation
            version = self.local_input_versions[object_id]
            cache_key = (self.owner_identity, generation, object_id, version)
            cache = self.service.tile_cache
            cached = cache.get(cache_key)
            if cached is not None:
                cache.move_to_end(cache_key)
                self.service.tile_cache_hits += 1
                self.metrics["cache_hit_count"] = self.metrics.get("cache_hit_count", 0) + 1
                self.metrics["cache_bytes_avoided"] = self.metrics.get("cache_bytes_avoided", 0) + len(cached)
                self.metrics["local_read_count"] += 1
                self.metrics["local_read_bytes"] += len(cached)
                return cached
            self.service.tile_cache_misses += 1
            self.metrics["cache_miss_count"] = self.metrics.get("cache_miss_count", 0) + 1
            from common.types.context import RequestContext
            context = RequestContext(identity=self.owner_identity,
                                     request_id=f"local-compute-{uuid4()}",
                                     coordinator_generation=self.service.coordinator_generation)
            data = await self.service.memory_node.read_sealed(
                context, object_id, self.owner_identity, self.service.coordinator_generation
            )
            self.metrics["local_read_count"] += 1
            self.metrics["local_read_bytes"] += len(data)
            limit = self.service.tile_cache_limit
            if limit and len(data) <= limit:
                while self.service.tile_cache_bytes + len(data) > limit and cache:
                    _, evicted = cache.popitem(last=False)
                    self.service.tile_cache_bytes -= len(evicted)
                cache[cache_key] = data
                self.service.tile_cache_bytes += len(data)
            return data
        return bytes((await self._call("read", object_id=object_id)).data)

    async def write(self, object_id: str, data: bytes) -> None:
        await self._call("write", object_id=object_id, data=data)

    async def free(self, object_id: str) -> None:
        await self._call("free", object_id=object_id)

    async def seal(self, object_id: str) -> None:
        await self._call("seal", object_id=object_id)
        self.metrics["seal_operation_count"] += 1
