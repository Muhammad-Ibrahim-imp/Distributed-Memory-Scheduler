import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import yaml
from memory_node.node import MemoryNode
from network.tcp.node_service import NodeService

async def main():
    node_id = os.environ.get("NODE_ID", "node-1")
    total_capacity = int(os.environ.get("NODE_CAPACITY_BYTES", 256 * 1024 * 1024))
    host = os.environ.get("SECURITY_HOST", "coordinator")
    port = int(os.environ.get("SECURITY_PORT", 8443))
    secret = os.environ.get("DSM_NODE_ENROLLMENT_SECRET", "")

    headroom_fraction = float(os.environ.get("NODE_HEADROOM_FRACTION", 0.10))
    headroom_min_bytes = int(os.environ.get("NODE_HEADROOM_MIN_BYTES", 134217728))

    mem_fraction = 0.5
    max_objects = 10000
    try:
        with open("configs/security.yaml", "r") as f:
            cfg = yaml.safe_load(f)
            limits = cfg.get("rate_limit", {}).get("node_level", {})
            mem_fraction = limits.get("max_memory_fraction_per_client", 0.5)
            max_objects = limits.get("max_objects_per_client", 10000)
    except FileNotFoundError:
        pass

    event_queue = asyncio.Queue(maxsize=100) # A2: Bound the queue

    def event_sink(event):
        try:
            event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass # Drop if full

    memory_node = MemoryNode(
        node_id=node_id, 
        total_capacity_bytes=total_capacity,
        max_memory_fraction=mem_fraction,
        max_objects=max_objects,
        headroom_fraction=headroom_fraction,
        headroom_min_bytes=headroom_min_bytes,
        event_sink=event_sink
    )
    
    service = NodeService(
        host=host, port=port, label=node_id,
        enrollment_secret=secret, memory_node=memory_node,
        event_queue=event_queue
    )
    
    print(f"[node] starting {node_id} targeting {host}:{port}")
    await service.run()

if __name__ == "__main__":
    asyncio.run(main())