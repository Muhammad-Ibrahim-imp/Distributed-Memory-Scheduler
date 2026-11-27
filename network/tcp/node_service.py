import asyncio
import json
import os
import time
from collections import OrderedDict
from contextlib import suppress

from common.protocol import dsm_pb2
from common.types.context import RequestContext
from common.types.errors import DSMError
from network.security.tls import client_context
from network.tcp.connection import Connection
from network.tcp.errors import error_to_code
from compute.task import ComputeResult, ComputeTask, TaskStatus
from compute.transport import CoordinatorDSMProxy


class PermanentAuthenticationError(ConnectionError):
    """The coordinator rejected an identity that must not reconnect blindly."""


class NodeService:
    def __init__(self, host, port, label, enrollment_secret, memory_node,
                 heartbeat_interval: float = 5.0, client_cert=None, client_key=None,
                 event_queue=None, server_hostname: str = "coordinator", compute_worker=None):
        self.host, self.port = host, port
        self.label, self.enrollment_secret = label, enrollment_secret
        self.client_cert, self.client_key = client_cert, client_key
        self.server_hostname = server_hostname
        self.event_queue = event_queue
        self.memory_node = memory_node
        self.compute_worker = compute_worker
        self.tile_cache_limit = max(0, int(os.getenv("DSM_WORKER_TILE_CACHE_BYTES", "67108864")))
        self.tile_cache = OrderedDict()
        self.tile_cache_bytes = 0
        self.tile_cache_hits = 0
        self.tile_cache_misses = 0
        if self.compute_worker is not None:
            self.compute_worker.authorize = lambda ctx, task: CoordinatorDSMProxy(
                self, ctx["task_id"], ctx["owner_identity"], task.input_object_ids,
                ctx.get("local_input_versions"),
            )
        self.heartbeat_interval = heartbeat_interval
        self.identity: str | None = None
        self.credential: str | None = None
        self.coordinator_generation = ""
        self.connection: Connection | None = None
        self._stop = asyncio.Event()
        self._refresh_lock = asyncio.Lock()

    async def run(self):
        while not self._stop.is_set():
            try:
                await self._connect_and_serve()
            except PermanentAuthenticationError as exc:
                print(f"[node] authentication permanently rejected: {exc}")
                break
            except (ConnectionError, OSError) as exc:
                print(f"[node] disconnected: {exc}; retry in 3s")
            await asyncio.sleep(3)

    async def _connect_and_serve(self):
        reader, writer = await asyncio.open_connection(
            self.host, self.port,
            ssl=client_context(self.client_cert, self.client_key),
            server_hostname=self.server_hostname,
        )
        conn = Connection(reader, writer)
        conn.set_handler(self._handle)
        conn.start()
        self.connection = conn
        try:
            if self.identity and self.credential:
                await self._refresh(conn)
            else:
                await self._register(conn)
            await self._refresh(conn)
        except BaseException:
            await conn.close()
            raise
        hb = asyncio.create_task(self._heartbeat_loop())
        ev = asyncio.create_task(self._event_sender_loop()) if self.event_queue is not None else None
        stopped = asyncio.create_task(self._stop.wait())
        try:
            await asyncio.wait((stopped, conn._reader_task), return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopped.cancel()
            with suppress(asyncio.CancelledError):
                await stopped
            hb.cancel()
            if ev is not None:
                ev.cancel()
            await conn.close()

    async def _refresh(self, conn: Connection):
        env = dsm_pb2.Envelope()
        env.protocol_version = 1
        env.timestamp = time.time()
        env.refresh.request_id = f"refresh-{time.time_ns()}"
        env.refresh.identity = self.identity or ""
        env.refresh.credential = self.credential or ""
        env.refresh.enrollment_secret = self.enrollment_secret
        env.refresh.coordinator_generation = self.coordinator_generation
        print(f"[auth] node refresh request={env.refresh.request_id} identity={self.identity} "
              f"prior_generation={self.coordinator_generation or '<missing>'}")
        resp = await conn.call(env)
        if resp.WhichOneof("payload") == "error":
            print(f"[auth] node refresh rejected request={env.refresh.request_id} "
                  f"identity={self.identity} code={resp.error.code}")
            if resp.error.code == "AUTH_REJECTED":
                raise PermanentAuthenticationError(
                    f"reauthentication rejected ({resp.error.code})")
            raise ConnectionError(f"reauthentication failed: {resp.error.message}")
        if not resp.coordinator_generation:
            raise ConnectionError("coordinator omitted its generation during reauthentication")
        if resp.refresh_response.credential:
            self.credential = resp.refresh_response.credential
        self.coordinator_generation = resp.coordinator_generation
        self.token = resp.refresh_response.token
        if self.compute_worker is not None:
            self.compute_worker.node_id = self.identity or self.label
        print(f"[auth] node refresh complete request={env.refresh.request_id} "
              f"identity={self.identity} generation={self.coordinator_generation}")
        print(f"[node] reauthenticated as {self.identity}")

    async def _register(self, conn: Connection):
        env = dsm_pb2.Envelope()
        env.protocol_version = 1
        env.timestamp = time.time()
        env.register.request_id = f"reg-{time.time_ns()}"
        env.register.enrollment_secret = self.enrollment_secret
        env.register.role = "node"
        env.register.label = self.label
        # This explicit path is required when a node process has lost its
        # one-time credential. The coordinator accepts it only for the
        # certificate's existing identity plus the configured enrollment factor.
        env.register.reauthenticate = True
        resp = await conn.call(env)
        if resp.WhichOneof("payload") == "error":
            if resp.error.code == "AUTH_REJECTED":
                raise PermanentAuthenticationError(f"registration rejected ({resp.error.code})")
            raise ConnectionError(f"register failed: {resp.error.message}")
        self.identity = resp.register_response.identity
        self.credential = resp.register_response.credential
        if not resp.coordinator_generation:
            raise ConnectionError("coordinator omitted its generation during registration")
        self.coordinator_generation = resp.coordinator_generation
        # A1: every registration issues a fresh identity; the node must use it.
        self.memory_node.update_node_identity(self.identity)
        if self.compute_worker is not None:
            self.compute_worker.node_id = self.identity
        print(f"[node] registered as {self.identity}")

    def _ctx(self, rid: str) -> RequestContext:
        return RequestContext(identity="coordinator", request_id=rid,
                              coordinator_generation=self.coordinator_generation)

    async def _handle(self, env: dsm_pb2.Envelope):
        field = env.WhichOneof("payload")
        if field == "compute_worker":
            await self._handle_compute(env)
            return
        if field not in ("alloc", "read", "write", "free", "seal"):
            return
        req = getattr(env, field)
        rid = req.request_id
        if (not env.coordinator_generation or
                env.coordinator_generation != self.coordinator_generation):
            resp = self._error(rid, ConnectionError("obsolete coordinator generation"))
            await self.connection.send(resp)
            return
        ctx = self._ctx(rid)
        print(f"[node {self.label}] {field} {req.object_id}")
        resp = dsm_pb2.Envelope()
        try:
            if field == "alloc":
                await self.memory_node.alloc(ctx, req.object_id, req.owner_identity, req.size)
                resp.alloc_response.request_id = rid
                resp.alloc_response.object_id = req.object_id
            elif field == "read":
                data = await self.memory_node.read(ctx, req.object_id, req.owner_identity)
                resp.read_response.request_id = rid
                resp.read_response.data = bytes(data)
            elif field == "write":
                await self.memory_node.write(ctx, req.object_id, req.owner_identity, req.data)
                resp.write_response.request_id = rid
            elif field == "seal":
                await self.memory_node.seal(ctx, req.object_id, req.owner_identity)
                resp.seal_response.request_id = rid
                resp.seal_response.object_id = req.object_id
            else:  # free
                await self.memory_node.free(ctx, req.object_id, req.owner_identity)
                resp.free_response.request_id = rid
        except Exception as exc:
            resp = self._error(rid, exc)
        await self.connection.send(resp)

    async def _handle_compute(self, env: dsm_pb2.Envelope):
        request = env.compute_worker
        rid = request.request_id
        if (self.compute_worker is None or not env.coordinator_generation
                or env.coordinator_generation != self.coordinator_generation):
            await self.connection.send(self._error(rid, ConnectionError("compute worker unavailable or obsolete generation")))
            return
        try:
            parameters = json.loads(request.parameters_json or "{}")
            if not isinstance(parameters, dict):
                raise ValueError("task parameters must be a JSON object")
            task = ComputeTask(
                task_id=request.task_id,
                workload_id=request.workload_id,
                operation=request.operation,
                input_object_ids=tuple(request.input_object_ids),
                parameters=parameters,
                estimated_memory_bytes=request.estimated_memory_bytes,
                dependencies=tuple(request.dependencies),
            )
            self.compute_worker.node_id = self.identity or self.label
            if len(request.local_input_object_ids) != len(request.input_versions):
                raise ValueError("local input authorization is malformed")
            context = {"task_id": task.task_id, "owner_identity": request.owner_identity,
                       "local_input_versions": dict(zip(request.local_input_object_ids,
                                                         request.input_versions))}
            result = await self.compute_worker.execute(task, context)
        except Exception as exc:
            result = ComputeResult(request.task_id, self.identity or self.label, TaskStatus.FAILED, error=str(exc))
        response = dsm_pb2.Envelope()
        response.compute_response.request_id = rid
        self._fill_compute_response(response.compute_response, result)
        await self.connection.send(response)

    @staticmethod
    def _fill_compute_response(response, result: ComputeResult):
        response.task_id = result.task_id
        response.node_id = result.node_id
        response.status = result.status.value
        response.output_object_ids.extend(result.output_object_ids)
        response.output_metadata_json = json.dumps(result.output_metadata, allow_nan=False)
        response.metrics.update(result.metrics)
        response.error = result.error or ""
        response.operation = result.operation
        response.worker_hostname = result.worker_hostname
        response.worker_pid = result.worker_pid
        response.execution_started_at = result.execution_started_at
        response.execution_finished_at = result.execution_finished_at

    def _error(self, rid: str, exc: Exception) -> dsm_pb2.Envelope:
        env = dsm_pb2.Envelope()
        env.error.request_id = rid
        env.error.ok = False
        env.error.message = str(exc)
        env.error.code = error_to_code(exc) if isinstance(exc, DSMError) else "INVALID_REQUEST"
        return env

    async def _heartbeat_loop(self):
        while not self._stop.is_set():
            try:
                report = await self.memory_node.get_node_report()
                env = dsm_pb2.Envelope()
                env.identity = self.identity or ""
                env.token = getattr(self, "token", "")
                env.nonce = f"hb-{time.time_ns()}"
                env.timestamp = time.time()
                env.coordinator_generation = self.coordinator_generation
                env.node_stats.request_id = f"hb-{time.time_ns()}"
                env.node_stats.node_id = self.identity or ""
                env.node_stats.total_memory = int(report.total_memory)
                env.node_stats.used_memory = int(report.used_memory)
                env.node_stats.available_memory = int(report.available_memory)
                env.node_stats.cpu_percent = float(report.cpu_percent)
                env.node_stats.object_count = int(report.object_count)
                env.node_stats.health = float(report.health)
                if self.compute_worker is not None:
                    env.node_stats.compute_operations.extend(sorted(self.compute_worker.registry.operations))
                    env.node_stats.compute_slots = self.compute_worker.max_workers
                    env.node_stats.compute_memory_bytes = self.compute_worker.max_working_memory_bytes
                await self.connection.send(env)
            except Exception as exc:
                print(f"[node] heartbeat error: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass

    async def _event_sender_loop(self):
        while not self._stop.is_set():
            event = await self.event_queue.get()
            env = dsm_pb2.Envelope()
            env.identity = self.identity or ""
            env.timestamp = time.time()
            proto_event = env.security_event
            proto_event.event_id = event.event_id
            proto_event.timestamp = event.timestamp
            proto_event.event_type = event.event_type.value
            proto_event.severity = event.severity.value
            proto_event.actor_id = event.actor_id
            proto_event.result = event.result.value
            proto_event.request_id = event.request_id
            for key, value in event.extra.items():
                proto_event.extra[key] = str(value)
            await self.connection.send(env)

    def stop(self):
        self._stop.set()
        if self.compute_worker is not None:
            self.compute_worker.close()
