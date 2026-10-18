import asyncio
import os
from security.bootstrap import build_security_core
from network.tcp.coordinator import CoordinatorServer, NodeRouter
from network.tcp.registry import NodeRegistry
from network.tcp.network_state import RegistryNetworkStateProvider
from network.tcp.bandwidth_probe import BandwidthProbe

# A2's runtime (waiting on A2 to push this)
try:
    from dsm.runtime import Runtime
except ImportError:
    Runtime = None

async def main():
    host = os.environ.get("SECURITY_HOST", "0.0.0.0")
    port = int(os.environ.get("SECURITY_PORT", 8443))

    # 1. Security (M5)
    core = build_security_core(set_default_audit=True)

    # 2. Network registry and router (B1)
    registry = NodeRegistry()
    router = NodeRouter(registry)
    
    # 3. A2's Runtime
    if Runtime is None:
        print("[coordinator] WARNING: dsm.runtime.Runtime not found. A2 hasn't pushed it yet.")
        print("[coordinator] Starting without runtime routing.")
        runtime = None
    else:
        runtime = Runtime(node_client=router)

    # 4. B1's server
    server = CoordinatorServer(
        host=host, port=port, auth_service=core.auth,
        runtime=runtime, registry=registry, trust=core.trust, audit=core.audit
    )
    await server.start()

    # 5. Start B1's bandwidth probe in the background
    probe = BandwidthProbe(registry)
    probe_task = asyncio.create_task(probe.run())

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        probe.stop()
        probe_task.cancel()
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())