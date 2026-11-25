import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import ssl
from security.bootstrap import build_security_core
from security.quarantine.trust_state import TrustStateMachine # M5's real class
from network.tcp.coordinator import CoordinatorServer, NodeRouter
from network.tcp.node_client import NodeClient
from network.tcp.registry import NodeRegistry
from network.tcp.bandwidth_probe import BandwidthProbe
from network.tcp.network_state import RegistryNetworkStateProvider
from network.tcp.resource_state import RegistryResourceProvider
from common.types.security import EventType, Result, Severity, TrustState

from dsm.runtime import Runtime
from dsm.directory.directory import Directory

from scheduler.config import load_scheduler_config
from scheduler.factory import create_scheduler

from common.interfaces.node_state import NodeStateProvider
from common.types.node import NodeStats


class SecurityWrapper:
    def __init__(self, core, trust_machine):
        self._core = core
        self._trust = trust_machine

    def check_access(self, identity, owner_identity, action, object_id):
        if identity and identity == owner_identity:
            return True
        self._core.audit.log_security_event(
            EventType.AUTHZ_DENIED, identity or "unknown", Severity.WARNING,
            Result.REJECTED, "-",
            object_id=object_id, action=str(action), reason="not_owner",
        )
        return False

    def register_revocation_listener(self, callback):
        pass

    def __getattr__(self, name):
        return getattr(self._core, name)


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

    core = build_security_core(set_default_audit=True)
    
    # M5 real trust machine
    trust_machine = TrustStateMachine(audit=core.audit)
    sec_wrapper = SecurityWrapper(core, trust_machine)

    registry = NodeRegistry()
    router = NodeRouter(registry)
    node_client = NodeClient(router)

    directory = Directory()

    res_provider = RegistryResourceProvider(registry)
    net_provider = RegistryNetworkStateProvider(registry)
    
    # Use real trust machine
    sec_provider = trust_machine
    node_provider = DynamicNodeProvider(registry, res_provider, net_provider, sec_provider)

    config = load_scheduler_config("configs/scheduler.json")
    scheduler = create_scheduler(config, node_state_provider=node_provider, security_provider=sec_provider)

    runtime = Runtime(
        directory=directory,
        scheduler=scheduler,
        security=sec_wrapper,
        node_client=node_client
    )

    server = CoordinatorServer(
        host=host, port=port, auth_service=core.auth,
        runtime=runtime, registry=registry, trust=sec_provider, audit=core.audit
    )

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