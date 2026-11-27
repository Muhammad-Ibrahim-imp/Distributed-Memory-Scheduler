import asyncio
from collections import defaultdict
from time import perf_counter
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
        self.on_close: Callable[[], Awaitable[None]] | None = None
        self._handlers: set[asyncio.Task] = set()
        self._wire_tx_bytes = 0
        self._wire_rx_bytes = 0
        self._requests_sent = 0
        self._responses_received = 0
        self._timeouts = 0
        self._call_seconds = 0.0
        self._tx_bytes_by_payload = defaultdict(int)
        self._rx_bytes_by_payload = defaultdict(int)

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
                field = env.WhichOneof("payload") or "unknown"
                self._wire_rx_bytes += len(raw) + 4
                self._rx_bytes_by_payload[field] += len(raw) + 4
                rid = request_id_of(env)
                fut = self._pending.pop(rid, None) if rid else None
                if fut is not None and not fut.done():
                    self._responses_received += 1
                    fut.set_result(env)
                elif self._handler is not None:
                    task = asyncio.create_task(self._handler(env))
                    self._handlers.add(task)
                    task.add_done_callback(self._handlers.discard)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("connection closed"))
            self._pending.clear()
            if self.on_close:
                await self.on_close()

    async def send(self, env: dsm_pb2.Envelope) -> None:
        raw = env.SerializeToString()
        await write_frame(self.writer, raw)
        field = env.WhichOneof("payload") or "unknown"
        self._wire_tx_bytes += len(raw) + 4
        self._tx_bytes_by_payload[field] += len(raw) + 4

    def stats(self) -> dict:
        """Return actual framed wire counters for this connection."""
        return {
            "wire_bytes_sent": self._wire_tx_bytes,
            "wire_bytes_received": self._wire_rx_bytes,
            "requests_sent": self._requests_sent,
            "responses_received": self._responses_received,
            "timeouts": self._timeouts,
            "call_duration_seconds": self._call_seconds,
            "wire_bytes_sent_by_payload": dict(self._tx_bytes_by_payload),
            "wire_bytes_received_by_payload": dict(self._rx_bytes_by_payload),
        }

    async def call(self, env: dsm_pb2.Envelope, timeout: float = DEFAULT_TIMEOUT) -> dsm_pb2.Envelope:
        rid = request_id_of(env)
        if rid is None:
            raise ValueError("request has no request_id")
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        started = perf_counter()
        self._requests_sent += 1
        try:
            await self.send(env)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._timeouts += 1
            self._pending.pop(rid, None)
            raise DSMTimeoutError(f"no response for request {rid}")
        except BaseException:
            self._pending.pop(rid, None)
            raise
        finally:
            self._call_seconds += perf_counter() - started

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        if self._reader_task and self._reader_task is not asyncio.current_task():
            await asyncio.gather(self._reader_task, return_exceptions=True)
        current = asyncio.current_task()
        handlers = tuple(task for task in self._handlers if task is not current)
        for task in handlers:
            task.cancel()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)
