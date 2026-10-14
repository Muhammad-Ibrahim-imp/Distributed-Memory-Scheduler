import asyncio
import time

from common.protocol import dsm_pb2
from common.types.errors import DSMError, ObjectNotFoundError
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
        print(f"[node] registered as {self.identity}")

    async def _handle(self, env: dsm_pb2.Envelope):
        field = env.WhichOneof("payload")
        if field == "read":
            rid = env.read.request_id
            try:
                data = await self.memory_node.load(env.read.object_id, env.read.owner_identity)
                resp = dsm_pb2.Envelope()
                resp.read_response.request_id = rid
                resp.read_response.data = data
            except Exception as exc:
                resp = self._error(rid, exc)
        elif field == "write":
            rid = env.write.request_id
            try:
                await self.memory_node.store(env.write.object_id, env.write.owner_identity, env.write.data)
                resp = dsm_pb2.Envelope()
                resp.write_response.request_id = rid
            except Exception as exc:
                resp = self._error(rid, exc)
        elif field == "free":
            rid = env.free.request_id
            try:
                await self.memory_node.delete(env.free.object_id, env.free.owner_identity)
            except ObjectNotFoundError:
                pass
            except Exception as exc:
                await self.connection.send(self._error(rid, exc))
                return
            resp = dsm_pb2.Envelope()
            resp.free_response.request_id = rid
        else:
            return
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
                stats = await self.memory_node.get_stats()
                env = dsm_pb2.Envelope()
                env.identity = self.identity or ""
                env.timestamp = time.time()
                env.node_stats.request_id = f"hb-{time.time_ns()}"
                env.node_stats.node_id = self.identity or ""
                env.node_stats.total_memory = int(getattr(stats, "total_memory", 0))
                env.node_stats.used_memory = int(getattr(stats, "used_memory", 0))
                env.node_stats.available_memory = int(getattr(stats, "available_memory", 0))
                env.node_stats.cpu_percent = float(getattr(stats, "cpu_percent", 0.0))
                env.node_stats.object_count = int(getattr(stats, "object_count", 0))
                env.node_stats.health = float(getattr(stats, "health", 1.0))
                await self.connection.send(env)
            except Exception as exc:
                print(f"[node] heartbeat error: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._stop.set()