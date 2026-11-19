import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio

from network.tcp.node_service import NodeService

# A1's real class
_MEMORY_NODE_IMPORT_ERROR = None
try:
    from memory_node.node import MemoryNode
except ImportError as exc:
    MemoryNode = None
    _MEMORY_NODE_IMPORT_ERROR = exc


async def main():
    node_id = os.environ.get("NODE_ID", "node-1")
    total_capacity = int(os.environ.get("NODE_CAPACITY_BYTES", 1024 * 1024 * 1024 * 8))
    host = os.environ.get("SECURITY_HOST", "coordinator")
    port = int(os.environ.get("SECURITY_PORT", 8443))
    secret = os.environ.get("DSM_NODE_ENROLLMENT_SECRET", "")

    if MemoryNode is None:
        print(f"[node] ERROR: could not import memory_node.node.MemoryNode: {_MEMORY_NODE_IMPORT_ERROR!r}")
        return

    # A1's node
    memory_node = MemoryNode(node_id, total_capacity)

    # B1's service wrapping it
    service = NodeService(
        host=host, port=port, label=node_id,
        enrollment_secret=secret, memory_node=memory_node
    )

    print(f"[node] starting {node_id} targeting {host}:{port}")
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())