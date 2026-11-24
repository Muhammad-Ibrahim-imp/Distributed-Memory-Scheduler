import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import yaml
from memory_node.node import MemoryNode
from network.tcp.node_service import NodeService

async def main():
    node_id = os.environ.get("NODE_ID", "node-1")
    
    # Default to 256MB to leave headroom inside the 512MB Docker container limit
    total_capacity = int(os.environ.get("NODE_CAPACITY_BYTES", 256 * 1024 * 1024))
    
    host = os.environ.get("SECURITY_HOST", "coordinator")
    port = int(os.environ.get("SECURITY_PORT", 8443))
    secret = os.environ.get("DSM_NODE_ENROLLMENT_SECRET", "")
    node_cert = os.environ.get("DSM_NODE_CERT_FILE", f"certs/nodes/{node_id}.pem")
    node_key = os.environ.get("DSM_NODE_KEY_FILE", f"certs/nodes/{node_id}.key")

    # Headroom settings from A1 (defaults: 10% and 128 MiB)
    headroom_fraction = float(os.environ.get("NODE_HEADROOM_FRACTION", 0.10))
    headroom_min_bytes = int(os.environ.get("NODE_HEADROOM_MIN_BYTES", 134217728))

    # Quota settings from M5's config
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

    # Create the queue for A1's events
    event_queue = asyncio.Queue()

    def event_sink(event):
        # Queue the event for the network layer to send over the wire
        event_queue.put_nowait(event)

    # A1's node. 
    memory_node = MemoryNode(
        node_id=node_id, 
        total_capacity_bytes=total_capacity,
        max_memory_fraction=mem_fraction,
        max_objects=max_objects,
        headroom_fraction=headroom_fraction,
        headroom_min_bytes=headroom_min_bytes,
        event_sink=event_sink
    )
    
    # B1's service wrapping it (passing the queue)
    service = NodeService(
        host=host, port=port, label=node_id,
        enrollment_secret=secret, memory_node=memory_node,
        event_queue=event_queue,
        client_cert=node_cert, client_key=node_key,
    )
    
    print(f"[node] starting {node_id} targeting {host}:{port}")
    await service.run()

if __name__ == "__main__":
    asyncio.run(main())
