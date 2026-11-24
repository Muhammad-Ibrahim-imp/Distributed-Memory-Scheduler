# common/interfaces/node_client.py
# the contract between A2's Object Manager and B1's network code
# A2 calls these methods, B1 builds the Envelope inside them and sends it.
# B1 must propagate ctx.coordinator_generation through the authenticated wire
# contract so A1 can reclaim allocations from obsolete coordinator generations.

from typing import Protocol

from common.types.context import RequestContext


class NodeClient(Protocol):
    # reserve space on the node, raises OutOfCapacityError if it will not fit
    async def alloc(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
        size: int,
    ) -> None: ...

    # return the stored bytes, raises ObjectNotFoundError for unknown id
    async def read(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
    ) -> bytes: ...

    # replace the whole value, raises ObjectNotFoundError if never allocated
    async def write(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
        data: bytes,
    ) -> None: ...

    # delete the object, silent no-op if it does not exist
    async def free(
        self,
        ctx: RequestContext,
        node_id: str,
        object_id: str,
        owner_identity: str,
    ) -> None: ...