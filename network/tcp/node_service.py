import asyncio
import time

from common.protocol import dsm_pb2
from common.types.context import RequestContext
from common.types.errors import DSMError, InvalidRequestError
from network.security.tls import client_context
from network.tcp.connection import Connection
from network.tcp.errors import error_to_code

class NodeService:
    def __init__(self, host, port, label, enrollment_secret, memory_node,
                 heartbeat_interval: float = 5.0, event_queue=None):
        self.host, self.port = host, port
        self.label, self.enrollment_secret = label, enrollment_secret
        self.memory_node = memory_node
        self.heartbeat_interval = heartbeat_interval
        self.event_queue = event_queue or asyncio.Queue(maxsize=100) # A2: Bounded
        self.identity: str | None = None
        self.credential: str | None = None
        self.coordinator_generation: str = ""
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
        ev = asyncio.create_task(self._event_sender_loop())
        
        try:
            await self._stop.wait()
        finally:
            hb.cancel()
            ev.cancel()
            await conn.close()

    async def _register(self, conn: Connection):
        env = dsm_pb2.Envelope()
        env.protocol_version = 1
        env.timestamp = time.time()
        env.register.request_id = f"reg-{time.time_ns()}"
        env.register.enrollment_secret = self.enrollment_secret
        env.register.role = "node"
        env.register.label = self.label
        env.register.client_nonce = f"nonce-{time.time_ns()}"
        resp = await conn.call(env)
        
        if resp.WhichOneof("payload") == "error":
            raise ConnectionError(f"register failed: {resp.error.message}")
            
        self.identity = resp.register_response.identity
        self.credential = resp.register_response.credential
        
        if hasattr(self.memory_node, "update_node_identity"):
            self.memory_node.update_node_identity(self.identity)
            
        print(f"[node] registered as {self.identity}")

    def _validate_id(self, val: str, name: str):
        # A2: 1 to 256 character limit
        if not isinstance(val, str) or not (1 <= len(val) <= 256):
            raise InvalidRequestError(f"{name} must be 1 to 256 characters")

    async def _handle(self, env: dsm_pb2.Envelope):
        field = env.WhichOneof("payload")
        if field not in ("alloc", "read", "write", "free"):
            return
            
        req = getattr(env, field)
        rid = req.request_id
        
        try:
            self._validate_id(req.object_id, "object_id")
            self._validate_id(req.owner_identity, "owner_identity")
            self._validate_id(env.identity, "identity")
        except InvalidRequestError as exc:
            await self.connection.send(self._error(rid, exc))
            return

        # A1: Generation propagation
        ctx = RequestContext(
            identity=env.identity,
            request_id=rid,
            coordinator_generation=env.coordinator_generation
        )
        
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
            else:
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

    async def _event_sender_loop(self):
        # A2: Event forwarding
        while not self._stop.is_set():
            try:
                event = await self.event_queue.get()
                env = dsm_pb2.Envelope()
                env.identity = self.identity or ""
                env.timestamp = time.time()
                
                proto_ev = env.security_event
                proto_ev.event_id = event.event_id
                proto_ev.timestamp = event.timestamp
                proto_ev.event_type = event.event_type.value
                proto_ev.severity = event.severity.value
                proto_ev.actor_id = event.actor_id
                proto_ev.result = event.result.value
                proto_ev.request_id = event.request_id
                for k, v in event.extra.items():
                    proto_ev.extra[k] = str(v)
                
                await self.connection.send(env)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[node] event sender error: {exc}")

    def stop(self):
        self._stop.set()