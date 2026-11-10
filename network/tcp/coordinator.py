import asyncio
import time
import hashlib
import ssl

from common.protocol import dsm_pb2
from common.types.errors import ObjectNotFoundError, TokenExpiredError, TokenInvalidError
from common.types.context import RequestContext
from common.types.security import EventType, Result, Severity, TrustState
from network.tcp.connection import Connection, DEFAULT_TIMEOUT
from network.tcp.registry import NodeRegistry
from network.security.rate_limit import RateLimiter
from network.security.replay import ReplayGuard


class CoordinatorServer:
    def __init__(self, host, port, auth_service, runtime, registry=None, trust=None, audit=None):
        self.host, self.port = host, port
        self.auth_service = auth_service
        self.runtime = runtime  # A2's runtime object
        self.registry = registry or NodeRegistry()
        self.trust = trust
        self.audit = audit
        self.replay = ReplayGuard()
        self.rate_limit = RateLimiter()
        self._server = None
        self._connections: dict[str, Connection] = {}

    async def start(self):
        from network.security.tls import server_context
        self._server = await asyncio.start_server(self._accept, self.host, self.port, ssl=server_context())
        print(f"[coordinator] listening on {self.host}:{self.port}")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for conn in list(self._connections.values()):
            await conn.close()

    async def force_close_connection(self, identity: str) -> None:
        conn = self._connections.pop(identity, None)
        if conn is not None:
            await conn.close()
        self.registry.unregister(identity)

    async def _accept(self, reader, writer):
        conn = Connection(reader, writer)
        # Extract cert fingerprint for M5
        fingerprint = ""
        ssl_obj = writer.get_extra_info('ssl_object')
        if ssl_obj:
            cert = ssl_obj.getpeercert(binary_form=True)
            if cert:
                fingerprint = hashlib.sha256(cert).hexdigest()
        
        conn.set_handler(lambda env, c=conn, f=fingerprint: self._handle(env, c, f))
        conn.start()

    async def _handle(self, env, conn, cert_fingerprint):
        field = env.WhichOneof("payload")

        if field == "register":
            await self._handle_register(env, conn, cert_fingerprint)
            return
        elif field == "refresh":
            # Handled in Week 5, no-op for now
            return
        elif field == "node_stats":
            self.registry.set_stats(env.node_stats.node_id, env.node_stats)
            return

        # Pipeline
        if not self._validate_envelope(env):
            await self._send_error(conn, env, "INVALID_REQUEST", "missing envelope fields")
            return

        if self.trust is not None and self.trust.get_trust_state(env.identity) is TrustState.REVOKED:
            await self._audit("authz_denied", env, "rejected", reason="revoked")
            await self._send_error(conn, env, "AUTH_REJECTED", "identity revoked")
            return

        try:
            claims = self.auth_service.validate(env.token, claimed_identity=env.identity)
            env.identity = claims.identity
        except TokenExpiredError:
            await self._send_error(conn, env, "TOKEN_EXPIRED", "token expired")
            return
        except TokenInvalidError:
            await self._audit("tamper_detected", env, "rejected")
            await self._send_error(conn, env, "TOKEN_INVALID", "token invalid")
            return
        except Exception:
            await self._audit("auth_failure", env, "rejected")
            await self._send_error(conn, env, "AUTH_REJECTED", "auth rejected")
            return

        r = self.replay.check(env.identity, env.nonce, env.timestamp)
        if not r.ok:
            event = "replay_detected" if r.reason == "replay_detected" else "timestamp_out_of_window"
            await self._audit(event, env, "rejected")
            await self._send_error(conn, env, "AUTH_REJECTED", r.reason)
            return

        if not self.rate_limit.check(env.identity):
            await self._audit("rate_limit_violation", env, "rejected")
            await self._send_error(conn, env, "RATE_LIMITED", "rate limited")
            return

        # Route to A2
        await self._route_to_runtime(env, conn)

    async def _route_to_runtime(self, env, conn):
        dsm = self.runtime.bind(env.identity)
        rid = self._rid_of(env)
        try:
            if env.WhichOneof("payload") == "allocate":
                obj_id = await dsm.alloc(env.allocate.size)
                resp = dsm_pb2.Envelope()
                resp.allocate_response.request_id = rid
                resp.allocate_response.object_id = obj_id
            elif env.WhichOneof("payload") == "read":
                data = await dsm.read(env.read.object_id)
                resp = dsm_pb2.Envelope()
                resp.read_response.request_id = rid
                resp.read_response.data = data
            elif env.WhichOneof("payload") == "write":
                await dsm.write(env.write.object_id, env.write.data)
                resp = dsm_pb2.Envelope()
                resp.write_response.request_id = rid
            elif env.WhichOneof("payload") == "free":
                await dsm.free(env.free.object_id)
                resp = dsm_pb2.Envelope()
                resp.free_response.request_id = rid
            else:
                return
            await conn.send(resp)
        except Exception as exc:
            from network.tcp.errors import error_to_code
            from common.types.errors import DSMError
            code = error_to_code(exc) if isinstance(exc, DSMError) else "INVALID_REQUEST"
            await self._send_error(conn, env, code, str(exc))

    async def _handle_register(self, env, conn, cert_fingerprint):
        rid = env.register.request_id
        try:
            # M5 noted register() will take cert_fingerprint later, passing for now
            result = self.auth_service.register(env.register.role, env.register.label, rid)
            resp = dsm_pb2.Envelope()
            resp.register_response.request_id = rid
            resp.register_response.identity = result.identity
            resp.register_response.credential = result.credential
            await conn.send(resp)
            self._connections[result.identity] = conn
            if env.register.role == "node":
                self.registry.register(result.identity, conn)
        except Exception as exc:
            await self._send_error(conn, env, "INVALID_REQUEST", str(exc))

    def _validate_envelope(self, env) -> bool:
        return bool(env.identity and env.token and env.nonce and env.timestamp)

    async def _send_error(self, conn, req, code, message):
        resp = dsm_pb2.Envelope()
        resp.error.request_id = self._rid_of(req) or "-"
        resp.error.code = code
        resp.error.message = message
        resp.error.ok = False
        await conn.send(resp)

    @staticmethod
    def _rid_of(env):
        field = env.WhichOneof("payload")
        if field is None:
            return None
        return getattr(getattr(env, field), "request_id", None)

    async def _audit(self, event_type_name: str, env, result: str, **extra):
        if self.audit is None:
            return
        try:
            self.audit.log_security_event(
                EventType(event_type_name), env.identity or "-",
                Severity.WARNING, Result(result), self._rid_of(env) or "-", **extra
            )
        except Exception:
            pass


class NodeRouter:
    def __init__(self, registry: NodeRegistry):
        self.registry = registry

    async def send(self, node_id: str, envelope: dsm_pb2.Envelope, timeout: float = DEFAULT_TIMEOUT):
        entry = self.registry.get(node_id)
        if entry is None:
            raise ObjectNotFoundError(f"node {node_id} not connected")
        t0 = time.perf_counter()
        resp = await entry.connection.call(envelope, timeout)
        self.registry.touch(node_id, rtt_ms=(time.perf_counter() - t0) * 1000.0)
        return resp