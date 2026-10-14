import asyncio
from typing import Awaitable, Callable

from common.protocol import dsm_pb2
from common.types.errors import DSMTimeoutError
from network.serialization.framing import read_frame, write_frame

DEFAULT_TIMEOUT = 10.0


def request_id_of(env: dsm_pb2.Envelope) -> str | None:
    field = env.WhichOneof("payload")
    if field is None:
        return None
    return getattr(getattr(env, field), "request_id", None)


class Connection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader, self.writer = reader, writer
        self._pending: dict[str, asyncio.Future] = {}
        self._handler: Callable[[dsm_pb2.Envelope], Awaitable[None]] | None = None
        self._reader_task: asyncio.Task | None = None
        self._closed = False

    def set_handler(self, handler):
        self._handler = handler

    def start(self):
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            while not self._closed:
                raw = await read_frame(self.reader)
                env = dsm_pb2.Envelope()
                env.ParseFromString(raw)
                rid = request_id_of(env)
                fut = self._pending.pop(rid, None) if rid else None
                if fut is not None and not fut.done():
                    fut.set_result(env)
                elif self._handler is not None:
                    asyncio.create_task(self._handler(env))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("connection closed"))
            self._pending.clear()

    async def send(self, env: dsm_pb2.Envelope) -> None:
        await write_frame(self.writer, env.SerializeToString())

    async def call(self, env: dsm_pb2.Envelope, timeout: float = DEFAULT_TIMEOUT) -> dsm_pb2.Envelope:
        rid = request_id_of(env)
        if rid is None:
            raise ValueError("request has no request_id")
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self.send(env)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise DSMTimeoutError(f"no response for request {rid}")
        except BaseException:
            self._pending.pop(rid, None)
            raise

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass