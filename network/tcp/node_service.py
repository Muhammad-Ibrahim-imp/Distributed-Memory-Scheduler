import asyncio
import time

from common.protocol import dsm_pb2
from common.types.context import RequestContext
from common.types.errors import DSMError
from network.security.tls import client_context
from network.tcp.connection import Connection
from network.tcp.errors import error_to_code


class NodeService:
    def __init__(self, host, port, label, enrollment_secret, memory_node,
                 heartbeat_interval: float = 5.0):
        self.host, self.port = host, port
        self.label, self.enrollment_secret = label, enrollment_secret
        self.memory_node = memory_node
        self.heartbeat_interval = heartbeat_interval
        self.identity: str | None = None
        self.credential: str | None = None
        self.connection: Connection | None = None
        self._stop = asyncio.Event()

    async def run(self):
        while not self._stop.is_set():
            try:
                await self._connect_and_serve()
            except (ConnectionError, OSError) as exc:
                print(f"[node] disconnected: {exc}; retry in 3s")
            await asyncio.sleep(3)

    async def _connect_and_serve(self):
        reader, writer = await asyncio.open_connection(
            self.host, self.port, ssl=client_context(), server_hostname="coordinator"
        )
        conn = Connection(reader, writer)
        conn.set_handler(self._handle)
        conn.start()
        self.connection = conn
        await self._register(conn)
        hb = asyncio.create_task(self._heartbeat_loop())
        try:
            await self._stop.wait()
        finally:
            hb.cancel()
            await conn.close()

    async def _register(self, conn: Connection):
        env = dsm_pb2.Envelope()
        env.protocol_version = 1
        env.timestamp = time.time()
        env.register.request_id = f"reg-{time.time_ns()}"
        env.register.enrollment_secret = self.enrollment_secret
        env.register.role = "node"
        env.register.label = self.label
        resp = await conn.call(env)
        if resp.WhichOneof("payload") == "error":
            raise ConnectionError(f"register failed: {resp.error.message}")
        self.identity = resp.register_response.identity
        self.credential = resp.register_response.credential
        # A1: every registration issues a fresh identity; the node must use it.
        self.memory_node.update_node_identity(self.identity)
        print(f"[node] registered as {self.identity}")

    def _ctx(self, rid: str) -> RequestContext:
        return RequestContext(identity="coordinator", request_id=rid)

    async def _handle(self, env: dsm_pb2.Envelope):
        field = env.WhichOneof("payload")
        if field not in ("alloc", "read", "write", "free"):
            return
        req = getattr(env, field)
        rid = req.request_id
        ctx = self._ctx(rid)
        print(f"[node {self.label}] {field} {req.object_id}")
        resp = dsm_pb2.Envelope()
        try:
            if field == "alloc":
                await self.memory_node.alloc(ctx, req.object_id, req.owner_identity, req.size)
                resp.allocate_response.request_id = rid
                resp.allocate_response.object_id = req.object_id
            elif field == "read":
                data = await self.memory_node.read(ctx, req.object_id, req.owner_identity)
                resp.read_response.request_id = rid
                resp.read_response.data = bytes(data)
            elif field == "write":
                await self.memory_node.write(ctx, req.object_id, req.owner_identity, req.data)
                resp.write_response.request_id = rid
            else:  # free
                await self.memory_node.free(ctx, req.object_id, req.owner_identity)
                resp.free_response.request_id = rid
        except Exception as exc:
            resp = self._error(rid, exc)
        await self.connection.send(resp)

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
                env.timestamp = time.time()
                env.node_stats.request_id = f"hb-{time.time_ns()}"
                env.node_stats.node_id = self.identity or ""
                env.node_stats.total_memory = int(report.total_memory)
                env.node_stats.used_memory = int(report.used_memory)
                env.node_stats.available_memory = int(report.available_memory)
                env.node_stats.cpu_percent = float(report.cpu_percent)
                env.node_stats.object_count = int(report.object_count)
                env.node_stats.health = float(report.health)
                await self.connection.send(env)
            except Exception as exc:
                print(f"[node] heartbeat error: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._stop.set()