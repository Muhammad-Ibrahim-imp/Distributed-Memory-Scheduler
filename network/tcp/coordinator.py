import asyncio
import time

from common.protocol import dsm_pb2
from common.types.errors import (
    ObjectNotFoundError,
    TokenExpiredError,
    TokenInvalidError,
)
from common.types.security import EventType, Result, Severity, TrustState
from network.security.rate_limit import RateLimiter
from network.security.replay import ReplayGuard
from network.security.tls import server_context
from network.tcp.connection import Connection, DEFAULT_TIMEOUT
from network.tcp.registry import NodeRegistry


class CoordinatorServer:
    def __init__(self, host, port, auth_service, registry=None, trust=None, audit=None):
        self.host, self.port = host, port
        self.auth_service = auth_service
        self.registry = registry or NodeRegistry()
        self.trust = trust      # M5's SecurityAPI (or fake)
        self.audit = audit      # M5's AuditLogger (or None)
        self.replay = ReplayGuard()
        self.rate_limit = RateLimiter()
        self._server = None
        self._connections: dict[str, Connection] = {}

    async def start(self):
        self._server = await asyncio.start_server(
            self._accept, self.host, self.port, ssl=server_context()
        )
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
        conn.set_handler(lambda env, c=conn: self._handle(env, c))
        conn.start()

    async def _handle(self, env, conn):
        field = env.WhichOneof("payload")

        if field == "register":
            await self._handle_register(env, conn)
            return
        if field == "refresh":
            return  # Week 5

        if field == "node_stats":
            self.registry.set_stats(env.node_stats.node_id, env.node_stats)
            return

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

        await self._send_error(conn, env, "INVALID_REQUEST", "week 4 routing not wired yet")

    async def _handle_register(self, env, conn):
        rid = env.register.request_id
        try:
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
            resp = dsm_pb2.Envelope()
            resp.error.request_id = rid
            resp.error.code = "INVALID_REQUEST"
            resp.error.message = str(exc)
            await conn.send(resp)

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
                EventType(event_type_name),
                env.identity or "-",
                Severity.WARNING,
                Result(result),
                self._rid_of(env) or "-",
                **extra,
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