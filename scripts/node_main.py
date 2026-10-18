import asyncio
import os
import sys

# A1's real class (A1 is still building this)
try:
    from memory_node.node import MemoryNode
except ImportError:
    MemoryNode = None

from network.tcp.node_service import NodeService

async def main():
    node_id = os.environ.get("NODE_ID", "node-1")
    total_capacity = int(os.environ.get("NODE_CAPACITY_BYTES", 1024 * 1024 * 1024 * 8))
    host = os.environ.get("SECURITY_HOST", "coordinator")
    port = int(os.environ.get("SECURITY_PORT", 8443))
    secret = os.environ.get("DSM_NODE_ENROLLMENT_SECRET", "")

    if MemoryNode is None:
        print("[node] ERROR: memory_node.node.MemoryNode not found. A1 hasn't pushed it yet.")
        print("[node] Starting without a real MemoryNode. (Waiting on A1).")
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