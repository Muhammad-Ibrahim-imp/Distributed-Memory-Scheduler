# common/interfaces/node_client.py
# the contract between A2's Object Manager and B1's network code
# A2 calls these methods, B1 builds the Envelope inside them and sends it

from typing import Protocol


class NodeClient(Protocol):
    # reserve space on the node, raises OutOfCapacityError if it will not fit
    async def alloc(self, ctx, node_id, object_id, owner_identity, size) -> None: ...

    # return the stored bytes, raises ObjectNotFoundError for unknown id
    async def read(self, ctx, node_id, object_id, owner_identity) -> bytes: ...

    # replace the whole value, raises ObjectNotFoundError if never allocated
    async def write(self, ctx, node_id, object_id, owner_identity, data: bytes) -> None: ...

    # delete the object, silent no-op if it does not exist
    async def free(self, ctx, node_id, object_id, owner_identity) -> None: ...