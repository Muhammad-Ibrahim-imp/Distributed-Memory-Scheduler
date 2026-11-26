import asyncio
import time
import hashlib
import ssl

from common.protocol import dsm_pb2
from common.types.errors import ObjectNotFoundError, TokenExpiredError, TokenInvalidError, DSMError
from common.types.context import RequestContext
from common.types.generation import CoordinatorGeneration
from common.types.security import EventType, Result, Severity, TrustState
from network.tcp.connection import Connection, DEFAULT_TIMEOUT
from network.tcp.registry import NodeRegistry
from network.security.rate_limit import RateLimiter
from network.security.replay import ReplayGuard
from network.tcp.errors import error_to_code
from security.authentication.enrollment import EnrollmentDenied, EnrollmentGuard
from security.authentication.registration import Role


class CoordinatorServer:
    def __init__(self, host, port, auth_service, runtime, registry=None, trust=None, audit=None,
                 enrollment: EnrollmentGuard | None = None,
                 generation: CoordinatorGeneration | None = None):
        if enrollment is None:
            raise ValueError("EnrollmentGuard is required")
        self.host, self.port = host, port
        self.auth_service = auth_service
        self.runtime = runtime
        self.registry = registry or NodeRegistry()
        self.trust = trust
        self.audit = audit
        self.enrollment = enrollment
        self.generation = generation or CoordinatorGeneration.create()
        runtime_generation = getattr(
            getattr(runtime, "object_manager", None), "coordinator_generation", None
        )
        if runtime_generation != self.generation.value:
            activate = getattr(runtime, "start_generation", None)
            if not callable(activate):
                raise ValueError("runtime must support coordinator generation activation")
            activate(self.generation.value)
        
        # Main post-auth rate limiter
        self.rate_limit = RateLimiter()
        # Pre-auth IP rate limiter for Register/Refresh
        self.pre_auth_limit = RateLimiter(capacity=20, refill_per_second=5.0)
        
        self.replay = ReplayGuard()
        self._server = None
        self._connections: dict[str, Connection] = {}
        self._identity_lock = asyncio.Lock()

    async def start(self):
        from network.security.tls import server_context
        self._server = await asyncio.start_server(self._accept, self.host, self.port, ssl=server_context())
        print(f"[coordinator] listening on {self.host}:{self.port} generation={self.generation.value}")

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
        
        # Extract IP for pre-auth rate limiting
        peer = writer.get_extra_info('peername')
        peer_ip = peer[0] if peer else "unknown"

        # Extract cert fingerprint for M5
        fingerprint = ""
        ssl_obj = writer.get_extra_info('ssl_object')
        if ssl_obj:
            cert = ssl_obj.getpeercert(binary_form=True)
            if cert:
                fingerprint = hashlib.sha256(cert).hexdigest()

        async def on_disconnect():
            for identity, active_conn in list(self._connections.items()):
                if active_conn is conn:
                    self._connections.pop(identity, None)
                    self.registry.unregister(identity)
                    break

        conn.on_close = on_disconnect
        conn.set_handler(lambda env, c=conn, f=fingerprint, ip=peer_ip: self._handle(env, c, f, ip))
        conn.start()

    async def _handle(self, env, conn, cert_fingerprint, peer_ip):
        field = env.WhichOneof("payload")

        # 1. Pre-auth IP rate limiting for Register and Refresh (Policy §9.3)
        if field in ("register", "refresh"):
            actor_id = f"ip:{peer_ip}"
            if not self.pre_auth_limit.check(actor_id):
                # Log without escalating trust state, using IP as actor_id
                await self._audit("rate_limit_violation", env, "rejected", actor_id=actor_id)
                await self._send_error(conn, env, "RATE_LIMITED", "pre-auth rate limit exceeded")
                return

        if field == "register":
            await self._handle_register(env, conn, cert_fingerprint, peer_ip)
            return
        elif field == "refresh":
            await self._handle_refresh(env, conn, cert_fingerprint)
            return
        elif field == "node_stats":
            self.registry.set_stats(env.node_stats.node_id, env.node_stats)
            return
        elif field == "security_event":
            if self.audit is not None:
                event = env.security_event
                try:
                    self.audit.log_security_event(
                        EventType(event.event_type), event.actor_id, Severity(event.severity),
                        Result(event.result), event.request_id, **dict(event.extra),
                    )
                except (ValueError, TypeError):
                    pass
            return

        # 2. Main Security Pipeline
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

        # 3. Post-auth Identity rate limiting
        if not self.rate_limit.check(env.identity):
            await self._audit("rate_limit_violation", env, "rejected")
            await self._send_error(conn, env, "RATE_LIMITED", "rate limited")
            return

        # Route to A2
        await self._route_to_runtime(env, conn)

    async def _route_to_runtime(self, env, conn):
        dsm = self.runtime.bind(env.identity)
        rid = self._rid_of(env)
        field = env.WhichOneof("payload")
        try:
            if field == "alloc":
                obj_id = await dsm.alloc(env.alloc.size)
                resp = dsm_pb2.Envelope()
                resp.alloc_response.request_id = rid
                resp.alloc_response.object_id = obj_id
            elif field == "read":
                data = await dsm.read(env.read.object_id)
                resp = dsm_pb2.Envelope()
                resp.read_response.request_id = rid
                resp.read_response.data = data
            elif field == "write":
                await dsm.write(env.write.object_id, env.write.data)
                resp = dsm_pb2.Envelope()
                resp.write_response.request_id = rid
            elif field == "free":
                await dsm.free(env.free.object_id)
                resp = dsm_pb2.Envelope()
                resp.free_response.request_id = rid
            else:
                return
            await conn.send(resp)
        except Exception as exc:
            code = error_to_code(exc) if isinstance(exc, DSMError) else "INVALID_REQUEST"
            await self._send_error(conn, env, code, str(exc))

    async def _handle_register(self, env, conn, cert_fingerprint, peer_ip):
        rid = env.register.request_id
        async with self._identity_lock:
            await self._register_locked(env, conn, cert_fingerprint, peer_ip, rid)

    async def _register_locked(self, env, conn, cert_fingerprint, peer_ip, rid):
        try:
            if (env.register.role != Role.NODE.value or
                    not env.register.reauthenticate):
                existing = None
            else:
                existing = self.enrollment.identity_for_reenrollment(
                    env.register.enrollment_secret, cert_fingerprint, env.register.label)
        except EnrollmentDenied:
            existing = None
            # Continue through first-enrollment authorization for unknown certificates;
            # known banned certificates remain denied there as well.
            try:
                self.enrollment.authorize(Role.NODE, env.register.enrollment_secret,
                                          cert_fingerprint, env.register.label)
            except EnrollmentDenied:
                await self._audit("enrollment_denied", env, "rejected",
                                  actor_id=f"ip:{peer_ip}", reason="registration_denied")
                await self._send_auth_error(conn, env, "registration denied")
                return
        if existing is not None:
            try:
                result = self.auth_service.reauthenticate_node(existing, rid)
                old = self._connections.get(existing)
                if old is not None and old is not conn:
                    await old.close()
                resp = dsm_pb2.Envelope()
                resp.register_response.request_id = rid
                resp.register_response.identity = result.identity
                resp.register_response.credential = result.credential
                resp.coordinator_generation = self.generation.value
                await conn.send(resp)
                self._connections[existing] = conn
                self.registry.register(existing, conn)
                print(f"[auth] returning node recovered identity={existing} "
                      f"generation={self.generation.value}")
            except Exception:
                await self._send_auth_error(conn, env, "registration denied")
            return
        try:
            role = self.enrollment.authorize(
                env.register.role, env.register.enrollment_secret, cert_fingerprint,
                env.register.label,
            )
        except EnrollmentDenied:
            await self._audit(
                "enrollment_denied", env, "rejected", actor_id=f"ip:{peer_ip}",
                reason="registration_denied",
            )
            await self._send_auth_error(conn, env, "registration denied")
            return

        try:
            result = self.auth_service.register(
                role, env.register.label, rid,
                certificate_fingerprint=cert_fingerprint if role is Role.NODE else None,
            )
            if role is Role.NODE:
                self.enrollment.bind_node(result.identity, cert_fingerprint)
            resp = dsm_pb2.Envelope()
            resp.register_response.request_id = rid
            resp.register_response.identity = result.identity
            resp.register_response.credential = result.credential
            resp.coordinator_generation = self.generation.value
            await conn.send(resp)
            self._connections[result.identity] = conn
            if role is Role.NODE:
                self.registry.register(result.identity, conn)
        except Exception as exc:
            await self._send_error(conn, env, "INVALID_REQUEST", str(exc))

    async def _handle_refresh(self, env, conn, cert_fingerprint):
        """Credential -> fresh token (policy section 3.3). Pre-auth message."""
        rid = env.refresh.request_id
        prior_generation = env.refresh.coordinator_generation
        recovery = bool(prior_generation and prior_generation != self.generation.value)
        print(f"[auth] refresh request={rid} identity={env.refresh.identity} "
              f"prior_generation={prior_generation or '<missing>'} "
              f"active_generation={self.generation.value} recovery={recovery}")
        if recovery:
            async with self._identity_lock:
                try:
                    bound_identity = self.enrollment.identity_for_reenrollment(
                        env.refresh.enrollment_secret, cert_fingerprint)
                    if bound_identity != env.refresh.identity:
                        raise EnrollmentDenied
                    print(f"[auth] recovery factors accepted request={rid} identity={bound_identity}")
                    registration = self.auth_service.reauthenticate_node(bound_identity, rid)
                    result = self.auth_service.authenticate(
                        bound_identity, registration.credential, rid)
                except Exception as exc:
                    print(f"[auth] recovery rejected request={rid} "
                          f"identity={env.refresh.identity} reason={type(exc).__name__}")
                    await self._send_auth_error(conn, env, "authentication failed")
                    return
                old = self._connections.get(bound_identity)
                if old is not None and old is not conn:
                    await old.close()
                self._connections[bound_identity] = conn
                self.registry.register(bound_identity, conn)
                resp = dsm_pb2.Envelope()
                resp.refresh_response.request_id = rid
                resp.refresh_response.token = result.token
                resp.refresh_response.credential = registration.credential
                resp.coordinator_generation = self.generation.value
                await conn.send(resp)
                print(f"[auth] recovery complete request={rid} identity={bound_identity} "
                      f"generation={self.generation.value}")
            return
        async with self._identity_lock:
            try:
                result = self.auth_service.authenticate(
                    env.refresh.identity, env.refresh.credential, rid
                )
            except Exception:
                print(f"[auth] refresh rejected request={rid} identity={env.refresh.identity} "
                      "reason=credential_or_trust")
                await self._send_auth_error(conn, env, "authentication failed")
                return
            if result.role is Role.NODE:
                try:
                    self.enrollment.authorize_node_reconnect(result.identity, cert_fingerprint)
                except Exception:
                    await self._audit("enrollment_denied", env, "rejected", reason="node_certificate_denied")
                    await self._send_auth_error(conn, env, "node certificate denied")
                    return
            old = self._connections.get(result.identity)
            if old is not None and old is not conn:
                await old.close()
            self._connections[result.identity] = conn
            if result.role is Role.NODE:
                self.registry.register(result.identity, conn)
            resp = dsm_pb2.Envelope()
            resp.refresh_response.request_id = rid
            resp.refresh_response.token = result.token
            resp.coordinator_generation = self.generation.value
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

    async def _send_auth_error(self, conn, req, message):
        await self._send_error(conn, req, "AUTH_REJECTED", message)
        close = getattr(conn, "close", None)
        if callable(close):
            await close()

    @staticmethod
    def _rid_of(env):
        field = env.WhichOneof("payload")
        if field is None:
            return None
        return getattr(getattr(env, field), "request_id", None)

    async def _audit(self, event_type_name: str, env, result: str, actor_id: str | None = None, **extra):
        if self.audit is None:
            return
        # Use IP for pre-auth events, identity for post-auth events
        actor = actor_id if actor_id else (env.identity or "-")
        try:
            self.audit.log_security_event(
                EventType(event_type_name), actor,
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
