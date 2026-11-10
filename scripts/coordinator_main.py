import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import ssl
from security.bootstrap import build_security_core
from network.tcp.coordinator import CoordinatorServer, NodeRouter
from network.tcp.registry import NodeRegistry
from network.tcp.bandwidth_probe import BandwidthProbe
from network.tcp.network_state import RegistryNetworkStateProvider
from common.types.security import EventType, Result, Severity, TrustState

# A2's Runtime and Directory
from dsm.runtime import Runtime
from dsm.directory.directory import Directory

# B2's Scheduler
from scheduler.config import load_scheduler_config
from scheduler.factory import create_scheduler

# Temporary A1 Dummy Provider
from common.interfaces.resource_state import ResourceStateProvider
from common.types.resource import ResourceStats

# B2 Interfaces
from common.interfaces.security_state import SecurityStateProvider
from common.interfaces.node_state import NodeStateProvider
from common.types.node import NodeStats

# --- TEMPORARY MOCKS ---
class DummyResourceProvider(ResourceStateProvider):
    def get_resource_state(self, node_id): 
        return ResourceStats(free_ram=8_000_000_000, cpu_usage=10.0, health=1.0)

class MockTrustProvider(SecurityStateProvider):
    def get_trust_state(self, node_id): 
        return TrustState.TRUSTED

class SecurityWrapper:
    """Wraps M5's SecurityCore to provide the revocation listener A2 expects."""
    def __init__(self, core):
        self._core = core

    def register_revocation_listener(self, callback):
        pass # Dummy method until M5 builds it

    def __getattr__(self, name):
        return getattr(self._core, name)

# --- B2 ADAPTERS ---
class DynamicNodeProvider(NodeStateProvider):
    def __init__(self, registry, res, net, sec):
        self.registry, self.res, self.net, self.sec = registry, res, net, sec
    def get_nodes(self):
        out = []
        for nid in self.registry.list_ids():
            try:
                r = self.res.get_resource_state(nid)
                n = self.net.get_network_state(nid)
                s = self.sec.get_trust_state(nid)
                out.append(NodeStats(
                    node_id=nid, free_ram=r.free_ram, cpu_usage=r.cpu_usage,
                    latency=n.latency, bandwidth=n.bandwidth, health=r.health, security_state=s
                ))
            except Exception:
                pass
        return out

async def main():
    host = os.environ.get("SECURITY_HOST", "0.0.0.0")
    port = int(os.environ.get("SECURITY_PORT", 8443))

    # 1. M5 Security
    core = build_security_core(set_default_audit=True)
    sec_wrapper = SecurityWrapper(core)

    # 2. B1 Network
    registry = NodeRegistry()
    router = NodeRouter(registry)

    # 3. A2 Directory
    directory = Directory()

    # 4. B2 Scheduler
    res_provider = DummyResourceProvider()
    net_provider = RegistryNetworkStateProvider(registry)
    sec_provider = MockTrustProvider()
    node_provider = DynamicNodeProvider(registry, res_provider, net_provider, sec_provider)

    config = load_scheduler_config("configs/scheduler.json")
    scheduler = create_scheduler(config, node_state_provider=node_provider, security_provider=sec_provider)

    # 5. A2 Runtime
    runtime = Runtime(
        directory=directory,
        scheduler=scheduler,
        security=sec_wrapper, # Passing the wrapper here
        node_client=router
    )

    # 6. B1 Server
    server = CoordinatorServer(
        host=host, port=port, auth_service=core.auth,
        runtime=runtime, registry=registry, trust=sec_provider, audit=core.audit
    )

    # 7. TLS Error Handler
    loop = asyncio.get_running_loop()
    def tls_error_handler(loop, context):
        exc = context.get("exception")
        if isinstance(exc, ssl.SSLError):
            transport = context.get("transport")
            addr = transport.get_extra_info("peername") if transport else None
            actor = f"ip:{addr[0] if addr else 'unknown'}"
            if core.audit:
                core.audit.log_security_event(
                    EventType.TLS_HANDSHAKE_FAILURE, actor, Severity.INFO,
                    Result.REJECTED, "-", reason=str(exc)
                )
        else:
            loop.default_exception_handler(context)
    loop.set_exception_handler(tls_error_handler)

    # 8. Start Server
    await server.start()
    probe = BandwidthProbe(registry)
    probe_task = asyncio.create_task(probe.run())

    print("[coordinator] running...")
    try:
        await asyncio.Event().wait()
    finally:
        probe.stop()
        probe_task.cancel()
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())