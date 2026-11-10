from common.protocol import dsm_pb2
from common.types.context import RequestContext
from network.tcp.coordinator import NodeRouter
from network.tcp.errors import code_to_error


class NodeClient:
    """Implements A2's NodeClient Protocol on top of B1's NodeRouter."""

    def __init__(self, router: NodeRouter):
        self.router = router

    async def alloc(self, ctx: RequestContext, node_id: str, object_id: str, owner_identity: str, size: int) -> None:
        env = dsm_pb2.Envelope()
        env.alloc.request_id = ctx.request_id
        env.alloc.object_id = object_id
        env.alloc.owner_identity = owner_identity
        env.alloc.size = size
        await self._send_and_raise(node_id, env)

    async def read(self, ctx: RequestContext, node_id: str, object_id: str, owner_identity: str) -> bytes:
        env = dsm_pb2.Envelope()
        env.read.request_id = ctx.request_id
        env.read.object_id = object_id
        env.read.owner_identity = owner_identity
        resp = await self._send_and_raise(node_id, env)
        return resp.read_response.data

    async def write(self, ctx: RequestContext, node_id: str, object_id: str, owner_identity: str, data: bytes) -> None:
        env = dsm_pb2.Envelope()
        env.write.request_id = ctx.request_id
        env.write.object_id = object_id
        env.write.owner_identity = owner_identity
        env.write.data = data
        await self._send_and_raise(node_id, env)

    async def free(self, ctx: RequestContext, node_id: str, object_id: str, owner_identity: str) -> None:
        env = dsm_pb2.Envelope()
        env.free.request_id = ctx.request_id
        env.free.object_id = object_id
        env.free.owner_identity = owner_identity
        await self._send_and_raise(node_id, env)

    async def _send_and_raise(self, node_id: str, env: dsm_pb2.Envelope) -> dsm_pb2.Envelope:
        resp = await self.router.send(node_id, env)
        if resp.WhichOneof("payload") == "error":
            raise code_to_error(resp.error.code, resp.error.message)
        return resp