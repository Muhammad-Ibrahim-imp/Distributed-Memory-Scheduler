import asyncio
import time

from common.protocol import dsm_pb2
from network.tcp.connection import Connection
from network.tcp.errors import code_to_error


class RemoteDSMAPI:
    def __init__(self, host, port, label, enrollment_secret, refresh_callback=None):
        self.host, self.port = host, port
        self.label, self.enrollment_secret = label, enrollment_secret
        self.identity: str | None = None
        self.token: str | None = None
        self.credential: str | None = None
        self.connection: Connection | None = None
        self.refresh_callback = refresh_callback
        self._counter = 0

    async def connect(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.connection = Connection(reader, writer)
        self.connection.start()
        await self._register()

    async def close(self):
        if self.connection:
            await self.connection.close()

    def _rid(self) -> str:
        self._counter += 1
        return f"r-{time.time_ns()}-{self._counter}"

    def _env(self) -> dsm_pb2.Envelope:
        env = dsm_pb2.Envelope()
        env.protocol_version = 1
        env.identity = self.identity or ""
        env.token = self.token or ""
        env.nonce = self._rid()
        env.timestamp = time.time()
        return env

    async def _register(self):
        env = self._env()
        env.register.request_id = self._rid()
        env.register.enrollment_secret = self.enrollment_secret
        env.register.role = "client"
        env.register.label = self.label
        resp = await self.connection.call(env)
        if resp.WhichOneof("payload") == "error":
            raise RuntimeError(f"register failed: {resp.error.message}")
        self.identity = resp.register_response.identity
        self.credential = resp.register_response.credential
        # Week 3 wires the real token flow.

    async def _call(self, env):
        resp = await self.connection.call(env)
        if resp.WhichOneof("payload") == "error":
            raise code_to_error(resp.error.code, resp.error.message)
        return resp

    async def alloc(self, size: int) -> str:
        env = self._env()
        env.allocate.request_id = self._rid()
        env.allocate.size = size
        return (await self._call(env)).allocate_response.object_id

    async def read(self, object_id: str) -> bytes:
        env = self._env()
        env.read.request_id = self._rid()
        env.read.object_id = object_id
        env.read.owner_identity = self.identity or ""
        return (await self._call(env)).read_response.data

    async def write(self, object_id: str, data: bytes) -> None:
        env = self._env()
        env.write.request_id = self._rid()
        env.write.object_id = object_id
        env.write.owner_identity = self.identity or ""
        env.write.data = data
        await self._call(env)

    async def free(self, object_id: str) -> None:
        env = self._env()
        env.free.request_id = self._rid()
        env.free.object_id = object_id
        env.free.owner_identity = self.identity or ""
        await self._call(env)