import asyncio
import pytest
from common.protocol import dsm_pb2
from common.types.errors import DSMTimeoutError
from network.tcp.connection import Connection


async def _pair():
    accepted = asyncio.Event()
    box = {}

    async def on_accept(reader, writer):
        box["sr"], box["sw"] = reader, writer
        accepted.set()

    server = await asyncio.start_server(on_accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cr, cw = await asyncio.open_connection("127.0.0.1", port)
    await accepted.wait()
    server.close()
    return Connection(cr, cw), Connection(box["sr"], box["sw"])


async def test_roundtrip():
    client, server = await _pair()
    client.start(); server.start()

    async def echo(env):
        resp = dsm_pb2.Envelope()
        resp.read_response.request_id = env.read.request_id
        resp.read_response.data = b"pong"
        await server.send(resp)

    server.set_handler(echo)
    env = dsm_pb2.Envelope()
    env.read.request_id = "req-1"
    env.read.object_id = "obj-1"
    resp = await client.call(env, timeout=2.0)
    assert resp.read_response.data == b"pong"
    await client.close(); await server.close()


async def test_timeout():
    client, server = await _pair()
    client.start(); server.start()
    env = dsm_pb2.Envelope()
    env.read.request_id = "req-x"
    with pytest.raises(DSMTimeoutError):
        await client.call(env, timeout=0.2)
    await client.close(); await server.close()


async def test_concurrent_100():
    client, server = await _pair()
    client.start(); server.start()

    async def echo(env):
        resp = dsm_pb2.Envelope()
        resp.read_response.request_id = env.read.request_id
        resp.read_response.data = env.read.request_id.encode()
        await server.send(resp)

    server.set_handler(echo)

    async def one(i):
        env = dsm_pb2.Envelope()
        env.read.request_id = f"r-{i}"
        return (await client.call(env, timeout=2.0)).read_response.data.decode()

    got = await asyncio.gather(*(one(i) for i in range(100)))
    assert got == [f"r-{i}" for i in range(100)]
    await client.close(); await server.close()