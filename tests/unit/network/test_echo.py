import asyncio
import time

from common.protocol import dsm_pb2
from network.serialization.framing import read_frame, write_frame


def build_envelope() -> dsm_pb2.Envelope:
    env = dsm_pb2.Envelope()
    env.protocol_version = 1
    env.identity = "test-node"
    env.token = "fake-token"
    env.nonce = "nonce-1"
    env.timestamp = time.time()
    env.read.request_id = "req-1"
    env.read.object_id = "obj-1"
    env.read.owner_identity = "test-node"
    return env


async def test_envelope_roundtrip():
    received = {}

    async def server(reader, writer):
        raw = await read_frame(reader)
        env = dsm_pb2.Envelope()
        env.ParseFromString(raw)
        received["envelope"] = env
        await write_frame(writer, env.SerializeToString())
        writer.close()

    srv = await asyncio.start_server(server, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    sent = build_envelope()
    await write_frame(writer, sent.SerializeToString())
    raw_back = await read_frame(reader)
    got = dsm_pb2.Envelope()
    got.ParseFromString(raw_back)
    writer.close()
    srv.close()
    await srv.wait_closed()

    assert received["envelope"].identity == "test-node"
    assert received["envelope"].read.object_id == "obj-1"
    assert got.identity == "test-node"
    assert got.read.request_id == "req-1"