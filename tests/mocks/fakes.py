# tests/mocks/fakes.py
# fake memory node used by A2 tests, mirrors the agreed node API
# alloc / read / write / free, ctx is always the first argument

from common.types.errors import (
    ObjectNotFoundError,
    OutOfCapacityError,
    InvalidRequestError,
)


class FakeNodeClient:
    def __init__(self, capacity_bytes=1024):
        self.capacity = capacity_bytes   # pretend RAM limit of this node
        self.used = 0                    # bytes reserved so far
        self.reserved = {}               # object_id -> size reserved at alloc
        self.data = {}                   # object_id -> stored bytes
        self.owners = {}                 # object_id -> owner_identity

    async def alloc(self, ctx, node_id, object_id, owner_identity, size):
        # reject at alloc time if the node cannot fit it, nothing waits until first write
        if self.used + size > self.capacity:
            raise OutOfCapacityError(f"node {node_id} is full")
        self.reserved[object_id] = size
        self.owners[object_id] = owner_identity
        self.data[object_id] = b""       # object exists but is empty
        self.used += size

    async def write(self, ctx, node_id, object_id, owner_identity, data):
        # write does NOT create objects, only alloc does
        if object_id not in self.data:
            raise ObjectNotFoundError(object_id)
        # data must fit in the space reserved at alloc
        if len(data) > self.reserved[object_id]:
            raise InvalidRequestError("data larger than allocated size")
        self.data[object_id] = data      # full replace, no partial writes

    async def read(self, ctx, node_id, object_id, owner_identity):
        # never allocated or already freed
        if object_id not in self.data:
            raise ObjectNotFoundError(object_id)
        return self.data[object_id]

   
    async def free(self, ctx, node_id, object_id, owner_identity):
        # free is idempotent, unknown or already freed id is a silent no-op
        if object_id not in self.data:
            return
        self.used -= self.reserved.pop(object_id)   # give the space back
        del self.data[object_id]
        del self.owners[object_id]