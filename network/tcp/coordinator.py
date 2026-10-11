import asyncio
import time

from common.protocol import dsm_pb2
from common.types.errors import ObjectNotFoundError
from network.tcp.connection import Connection, DEFAULT_TIMEOUT
from network.tcp.registry import NodeRegistry


class CoordinatorServer:
    def __init__(self, host, port, auth_service, registry=None):
        self.host, self.port = host, port
        self.auth_service = auth_service
        self.registry = registry or NodeRegistry()
        self._server = None
        self._connections: dict[str, Connection] = {}

    async def start(self):
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        print(f"[coordinator] listening on {self.host}:{self.port}")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for conn in list(self._connections.values()):
            await conn.close()

    async def force_close_connection(self, identity: str) -> None:
        """Called in-process by M5 when an identity is REVOKED."""
        conn = self._connections.pop(identity, None)
        if conn is not None:
            await conn.close()
        self.registry.unregister(identity)

    async def _accept(self, reader, writer):
        conn = Connection(reader, writer)
        conn.set_handler(lambda env, c=conn: self._handle(env, c))
        conn.start()

    async def _handle(self, env, conn):
        field = env.WhichOneof("payload")
        if field == "register":
            await self._handle_register(env, conn)
        elif field == "node_stats":
            self.registry.set_stats(env.node_stats.node_id, env.node_stats)
        # Week 3 adds envelope validation, auth, replay, rate-limit here.

    async def _handle_register(self, env, conn):
        rid = env.register.request_id
        try:
            result = self.auth_service.register(env.register.role, env.register.label, rid)
            resp = dsm_pb2.Envelope()
            resp.register_response.request_id = rid
            resp.register_response.identity = result.identity
            resp.register_response.credential = result.credential
            await conn.send(resp)
            self._connections[result.identity] = conn
            if env.register.role == "node":
                self.registry.register(result.identity, conn)
        except Exception as exc:
            resp = dsm_pb2.Envelope()
            resp.error.request_id = rid
            resp.error.code = "INVALID_REQUEST"
            resp.error.message = str(exc)
            await conn.send(resp)


class NodeRouter:
    def __init__(self, registry: NodeRegistry):
        self.registry = registry

    async def send(self, node_id: str, envelope: dsm_pb2.Envelope, timeout: float = DEFAULT_TIMEOUT):
        entry = self.registry.get(node_id)
        if entry is None:
            raise ObjectNotFoundError(f"node {node_id} not connected")
        t0 = time.perf_counter()
        resp = await entry.connection.call(envelope, timeout)
        self.registry.touch(node_id, rtt_ms=(time.perf_counter() - t0) * 1000.0)
        return resp