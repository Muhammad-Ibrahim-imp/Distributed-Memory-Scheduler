import asyncio
import pytest

from network.serialization.framing import MAX_FRAME_SIZE, read_frame, write_frame


async def _roundtrip(data: bytes) -> bytes:
    server_done = asyncio.Event()
    result = {}

    async def server(reader, writer):
        result["payload"] = await read_frame(reader)
        server_done.set()
        writer.close()

    srv = await asyncio.start_server(server, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await write_frame(writer, data)
    await server_done.wait()
    writer.close()
    srv.close()
    await srv.wait_closed()
    return result["payload"]


async def test_normal_roundtrip():
    assert await _roundtrip(b"hello") == b"hello"


async def test_empty_payload():
    assert await _roundtrip(b"") == b""


async def test_truncated_frame_raises():
    async def handler(reader, writer):
        writer.write(b"\x00\x00\x00\x10" + b"short")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)
    writer.close()
    srv.close()
    await srv.wait_closed()


async def test_oversized_frame_rejected_on_write():
    big = b"x" * (MAX_FRAME_SIZE + 1)

    async def handler(reader, writer):
        writer.close()

    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    _, writer = await asyncio.open_connection("127.0.0.1", port)
    with pytest.raises(ValueError):
        await write_frame(writer, big)
    writer.close()
    srv.close()
    await srv.wait_closed()