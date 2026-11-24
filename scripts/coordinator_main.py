import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import ssl
from security.bootstrap import build_security_core
from security.config import load_security_config
from security.authentication.enrollment import EnrollmentGuard
from security.persistence import SecurityRepository
from security.persistence.database import SecurityDatabase, SecurityPersistenceError
from security.quarantine.trust_state import TrustStateMachine
from network.tcp.coordinator import CoordinatorServer, NodeRouter
from network.tcp.node_client import NodeClient
from network.tcp.registry import NodeRegistry
from network.tcp.bandwidth_probe import BandwidthProbe
from network.tcp.network_state import RegistryNetworkStateProvider
from network.tcp.resource_state import RegistryResourceProvider
from common.types.security import EventType, Result, Severity

# A2's Runtime and Directory
from dsm.runtime import Runtime
from dsm.directory.directory import Directory

# B2's Scheduler
from scheduler.config import load_scheduler_config
from scheduler.factory import create_scheduler

# B2 Interfaces
from common.interfaces.node_state import NodeStateProvider
from common.types.node import NodeStats


class SecurityWrapper:
    """Gives A2's Runtime the two things it calls on `security`:
    check_access(...) and register_revocation_listener(...).
    Everything else is forwarded to M5's SecurityCore."""
    def __init__(self, core, trust):
        self._core, self._trust = core, trust

    def check_access(self, identity, owner_identity, action, object_id):
        """Ownership rule (policy section 8): only the owner may read/write/free."""
        if identity and identity == owner_identity:
            return True
        self._core.audit.log_security_event(
            EventType.AUTHZ_DENIED, identity or "unknown", Severity.WARNING,
            Result.REJECTED, "-",
            object_id=object_id, action=str(action), reason="not_owner",
        )
        return False

    def register_revocation_listener(self, callback):
        self._trust.register_revocation_listener(callback)

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
    security_config = load_security_config("configs/security.yaml")
    if security_config.persistence.backend != "sqlite":
        raise SecurityPersistenceError("unsupported security persistence backend")
    database = SecurityDatabase(security_config.persistence.database_path)
    repository = SecurityRepository(database)
    enrollment = EnrollmentGuard(security_config.enrollment, repository=repository)
    trust_state = TrustStateMachine(security_config.trust_state, repository=repository)
    core = build_security_core(
        config=security_config, trust=trust_state, set_default_audit=True,
        repository=repository,
    )
    trust_state.attach_audit(core.audit)
    core.audit.emit(EventType.COORDINATOR_STARTUP, "coordinator", Result.SUCCESS, "-")
    sec_wrapper = SecurityWrapper(core, trust_state)

    # 2. B1 Network
    registry = NodeRegistry()
    router = NodeRouter(registry)
    node_client = NodeClient(router)   # A2's NodeClient protocol on top of B1's router

    # 3. A2 Directory
    directory = Directory()

    # 4. B2 Scheduler (real per-node resources from heartbeats)
    res_provider = RegistryResourceProvider(registry)
    net_provider = RegistryNetworkStateProvider(registry)
    sec_provider = trust_state
    node_provider = DynamicNodeProvider(registry, res_provider, net_provider, sec_provider)

    config = load_scheduler_config("configs/scheduler.json")
    scheduler = create_scheduler(config, node_state_provider=node_provider, security_provider=sec_provider)

    # 5. A2 Runtime
    runtime = Runtime(
        directory=directory,
        node_client=node_client,
        scheduler=scheduler,
        security=sec_wrapper,
    )

    # 6. B1 Server
    server = CoordinatorServer(
        host=host, port=port, auth_service=core.auth,
        runtime=runtime, registry=registry, trust=sec_provider, audit=core.audit,
        enrollment=enrollment,
    )

    # 7. TLS Error Handler
    loop = asyncio.get_running_loop()

    trust_state.set_force_close_connection(server.force_close_connection)
    trust_state.register_revocation_listener(enrollment.record_revocation)
    trust_state.register_revocation_listener(
        lambda identity, _timestamp: core.auth.revoke_identity(identity, "trust_state_revoked")
    )
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
        core.audit.close()
        repository.close()


if __name__ == "__main__":
    asyncio.run(main())
