import asyncio
import json
import time
import hashlib
import os
import ssl
from dataclasses import replace
from uuid import uuid4

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
from compute.task import ComputeResult, ComputeTask, TaskStatus


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
        # Compute RPCs fan out across tasks and are already constrained by
        # task ownership, active-task quotas, worker slots, and object grants.
        self.compute_rate_limit = RateLimiter(capacity=64, refill_per_second=32.0)
        # Pre-auth IP rate limiter for Register/Refresh
        self.pre_auth_limit = RateLimiter(capacity=20, refill_per_second=5.0)
        
        self.replay = ReplayGuard()
        self._server = None
        self._connections: dict[str, Connection] = {}
        self._identity_lock = asyncio.Lock()
        self._compute_tasks: dict[str, dict] = {}
        self._compute_grants: dict[str, dict] = {}
        self._compute_active: dict[str, int] = {}
        self.compute_scheduler_policy = os.environ.get("DSM_COMPUTE_SCHEDULER", "locality").lower()
        if self.compute_scheduler_policy not in {"locality", "round_robin"}:
            raise ValueError("DSM_COMPUTE_SCHEDULER must be locality or round_robin")
        self._compute_round_robin = 0

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
        limiter = (self.compute_rate_limit if field in (
            "compute_submit", "compute_status", "compute_cancel", "compute_object_access"
        ) else self.rate_limit)
        if not limiter.check(env.identity):
            await self._audit("rate_limit_violation", env, "rejected")
            await self._send_error(conn, env, "RATE_LIMITED", "rate limited")
            return

        if field == "node_stats":
            if (env.identity != env.node_stats.node_id
                    or self._connections.get(env.identity) is not conn):
                await self._send_error(conn, env, "AUTH_REJECTED", "node stats identity mismatch")
                return
            self.registry.set_stats(env.identity, env.node_stats)
            return

        # Route to A2
        await self._route_to_runtime(env, conn)

    async def _route_to_runtime(self, env, conn):
        rid = self._rid_of(env)
        field = env.WhichOneof("payload")
        if field in ("compute_submit", "compute_status", "compute_cancel", "compute_object_access") and (
            env.coordinator_generation != self.generation.value
        ):
            await self._send_error(conn, env, "AUTH_REJECTED", "obsolete coordinator generation")
            return
        if field == "seal" and env.coordinator_generation != self.generation.value:
            await self._send_error(conn, env, "AUTH_REJECTED", "obsolete coordinator generation")
            return
        if field == "compute_submit":
            await self._submit_compute(env, conn)
            return
        if field == "compute_status":
            await self._compute_status(env, conn)
            return
        if field == "compute_cancel":
            await self._compute_cancel(env, conn)
            return
        if field == "compute_object_access":
            await self._compute_object_access(env, conn)
            return
        if field == "cluster_stats_request":
            response = dsm_pb2.Envelope()
            response.cluster_stats_report.request_id = env.cluster_stats_request.request_id
            for node_id in self.registry.list_ids():
                entry = self.registry.get(node_id)
                if entry is None:
                    continue
                node_report = response.cluster_stats_report.nodes.add()
                if entry.last_stats is not None:
                    node_report.CopyFrom(entry.last_stats)
                else:
                    node_report.node_id = node_id
                connection_stats = entry.connection.stats()
                node_report.coordinator_wire_bytes_sent = connection_stats["wire_bytes_sent"]
                node_report.coordinator_wire_bytes_received = connection_stats["wire_bytes_received"]
                node_report.coordinator_wire_bytes_sent_by_payload.update(
                    connection_stats["wire_bytes_sent_by_payload"]
                )
                node_report.coordinator_wire_bytes_received_by_payload.update(
                    connection_stats["wire_bytes_received_by_payload"]
                )
            await conn.send(response)
            return
        dsm = self.runtime.bind(env.identity)
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
            elif field == "seal":
                await dsm.seal(env.seal.object_id)
                resp = dsm_pb2.Envelope()
                resp.seal_response.request_id = rid
                resp.seal_response.object_id = env.seal.object_id
                record = self.runtime.object_manager.directory.get(env.seal.object_id)
                resp.seal_response.version = record.version
            else:
                return
            await conn.send(resp)
        except Exception as exc:
            code = error_to_code(exc) if isinstance(exc, DSMError) else "INVALID_REQUEST"
            await self._send_error(conn, env, code, str(exc))

    async def _submit_compute(self, env, conn):
        request = env.compute_submit
        try:
            if (not request.task_id or not request.workload_id or not request.operation
                    or len(request.parameters_json.encode("utf-8")) > 1024 * 1024
                    or len(request.input_object_ids) > 256 or len(self._compute_tasks) >= 10000):
                raise ValueError("invalid, oversized, or over-quota compute task")
            parameters = json.loads(request.parameters_json or "{}")
            if not isinstance(parameters, dict):
                raise ValueError("task parameters must be a JSON object")
            task = ComputeTask(
                request.task_id, request.workload_id, request.operation,
                tuple(request.input_object_ids), parameters,
                int(request.estimated_memory_bytes), request.estimated_compute_cost,
                tuple(request.dependencies),
            )
            if task.task_id in self._compute_tasks:
                raise ValueError("duplicate task_id")
            for dependency in task.dependencies:
                prior = self._compute_tasks.get(dependency)
                if prior is None or prior["owner"] != env.identity:
                    raise ValueError("dependencies must belong to this identity and be submitted first")
            active_for_owner = sum(
                item["owner"] == env.identity and item["status"] in
                (TaskStatus.QUEUED, TaskStatus.RUNNING)
                for item in self._compute_tasks.values()
            )
            if active_for_owner >= 32:
                raise ValueError("identity compute task limit exceeded")
            directory = self.runtime.object_manager.directory
            for object_id in task.input_object_ids:
                record = directory.get(object_id)
                if record is None or record.owner_identity != env.identity:
                    raise ObjectNotFoundError(object_id)
                if not record.sealed:
                    raise ValueError(f"compute input is not sealed: {object_id}")
            entry = {"task": task, "owner": env.identity, "status": TaskStatus.QUEUED,
                     "result": None, "node_id": "", "future": None,
                     "pin_operation_count": 0, "release_operation_count": 0,
                     "direct_local_reads": bool(request.direct_local_reads)}
            self._compute_tasks[task.task_id] = entry
            entry["future"] = asyncio.create_task(self._run_compute(task.task_id))
            await self._send_compute_response(conn, request.request_id, task.task_id, TaskStatus.QUEUED)
        except Exception as exc:
            await self._send_error(conn, env, "INVALID_REQUEST", str(exc))

    async def _run_compute(self, task_id: str):
        entry = self._compute_tasks[task_id]
        task, owner = entry["task"], entry["owner"]
        pinned_ids: list[str] = []
        try:
            pinned_inputs = {}
            manager = self.runtime.object_manager
            for object_id in task.input_object_ids:
                record, version = await manager.pin_readonly(owner, object_id)
                pinned_ids.append(object_id)
                entry["pin_operation_count"] += 1
                pinned_inputs[object_id] = (record, version)
            for dependency in task.dependencies:
                prior = self._compute_tasks[dependency]
                await prior["future"]
                if prior["status"] is not TaskStatus.COMPLETED:
                    raise RuntimeError(f"dependency {dependency!r} did not complete")
            local = {}
            directory = self.runtime.object_manager.directory
            for object_id in task.input_object_ids:
                record = directory.get(object_id)
                if record is not None:
                    local[record.node_id] = local.get(record.node_id, 0) + record.size
            capable = []
            candidates = []
            deadline = time.monotonic() + 300.0
            discovery_deadline = time.monotonic() + 5.0
            while not candidates:
                capable.clear()
                for candidate_id in self.registry.list_ids():
                    node_entry = self.registry.get(candidate_id)
                    if (node_entry is None or node_entry.last_stats is None
                            or time.time() - node_entry.last_seen > 15.0):
                        continue
                    stats = node_entry.last_stats
                    if task.operation not in stats.compute_operations:
                        continue
                    if stats.compute_memory_bytes and task.estimated_memory_bytes > stats.compute_memory_bytes:
                        continue
                    # available_memory is the node's DSM storage pool, while
                    # compute_memory_bytes is the worker's declared RAM budget.
                    # Compare task working memory only to the latter.
                    if self.trust is not None and self.trust.get_trust_state(candidate_id) is not TrustState.TRUSTED:
                        continue
                    capable.append(candidate_id)
                    if int(stats.compute_slots) > self._compute_active.get(candidate_id, 0):
                        candidates.append(candidate_id)
                if candidates:
                    break
                if (not capable and time.monotonic() >= discovery_deadline) or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.05)
            if not candidates:
                snapshot = []
                for candidate_id in self.registry.list_ids():
                    node_entry = self.registry.get(candidate_id)
                    stats = node_entry.last_stats if node_entry is not None else None
                    snapshot.append({
                        "node_id": candidate_id,
                        "stats_fresh": bool(node_entry and stats and time.time() - node_entry.last_seen <= 15.0),
                        "operations": list(stats.compute_operations) if stats else [],
                        "slots": int(stats.compute_slots) if stats else 0,
                        "active": self._compute_active.get(candidate_id, 0),
                        "compute_memory": int(stats.compute_memory_bytes) if stats else 0,
                        "available_memory": int(stats.available_memory) if stats else 0,
                        "trust": self.trust.get_trust_state(candidate_id).value if self.trust else "unknown",
                    })
                raise RuntimeError(
                    f"no trusted available worker supports {task.operation!r}; "
                    f"task={task.task_id} workers={json.dumps(snapshot, sort_keys=True)}"
                )
            if self.compute_scheduler_policy == "round_robin":
                ordered = sorted(candidates)
                node_id = ordered[self._compute_round_robin % len(ordered)]
                self._compute_round_robin += 1
            else:
                node_id = max(candidates, key=lambda nid: (local.get(nid, 0),
                                                             -self._compute_active.get(nid, 0), nid))
            node = self.registry.get(node_id)
            entry["node_id"], entry["status"] = node_id, TaskStatus.RUNNING
            self._compute_active[node_id] = self._compute_active.get(node_id, 0) + 1
            self._compute_grants[task_id] = {
                "node_id": node_id, "owner": owner, "inputs": frozenset(task.input_object_ids),
                "outputs": set(), "connection": node.connection,
            }
            request = dsm_pb2.Envelope()
            request.coordinator_generation = self.generation.value
            worker_req = request.compute_worker
            worker_req.request_id = f"worker-{uuid4()}"
            worker_req.task_id = task.task_id
            worker_req.workload_id = task.workload_id
            worker_req.owner_identity = owner
            worker_req.operation = task.operation
            worker_req.input_object_ids.extend(task.input_object_ids)
            local_inputs = [(oid, version) for oid, (record, version) in pinned_inputs.items()
                            if entry["direct_local_reads"] and record.node_id == node_id]
            worker_req.local_input_object_ids.extend(oid for oid, _ in local_inputs)
            worker_req.input_versions.extend(version for _, version in local_inputs)
            worker_req.parameters_json = json.dumps(task.parameters, allow_nan=False)
            worker_req.estimated_memory_bytes = task.estimated_memory_bytes
            worker_req.dependencies.extend(task.dependencies)
            response = await node.connection.call(request, timeout=300.0)
            if response.WhichOneof("payload") != "compute_response":
                raise RuntimeError("worker returned an invalid compute response")
            data = response.compute_response
            if data.task_id != task.task_id or data.node_id != node_id or data.operation != task.operation:
                raise RuntimeError("worker response does not match assigned task")
            result = ComputeResult(
                data.task_id, data.node_id, TaskStatus(data.status),
                tuple(data.output_object_ids), json.loads(data.output_metadata_json or "{}"),
                dict(data.metrics), data.error or None, data.operation, data.worker_hostname,
                int(data.worker_pid), data.execution_started_at, data.execution_finished_at,
            )
            grant = self._compute_grants[task_id]
            if not set(result.output_object_ids).issubset(grant["outputs"]):
                raise RuntimeError("worker returned output objects outside its task grant")
            entry["result"], entry["status"] = result, result.status
            if result.status is not TaskStatus.COMPLETED:
                await self._cleanup_compute_outputs(task_id)
        except asyncio.CancelledError:
            entry["status"] = TaskStatus.CANCELLED
            await self._cleanup_compute_outputs(task_id)
            raise
        except Exception as exc:
            entry["result"] = ComputeResult(task_id, entry["node_id"], TaskStatus.FAILED, error=str(exc))
            entry["status"] = TaskStatus.FAILED
            await self._cleanup_compute_outputs(task_id)
        finally:
            grant = self._compute_grants.pop(task_id, None)
            if grant is not None:
                self._compute_active[grant["node_id"]] -= 1
            for object_id in pinned_ids:
                await self.runtime.object_manager.unpin(object_id)
                entry["release_operation_count"] += 1
            if entry["result"] is not None:
                metrics = dict(entry["result"].metrics)
                metrics["pin_operation_count"] = float(entry["pin_operation_count"])
                metrics["release_operation_count"] = float(entry["release_operation_count"])
                entry["result"] = replace(entry["result"], metrics=metrics)

    async def _compute_object_access(self, env, conn):
        request = env.compute_object_access
        grant = self._compute_grants.get(request.task_id)
        if (grant is None or grant["node_id"] != env.identity
                or grant["connection"] is not conn or self._connections.get(env.identity) is not conn):
            await self._send_error(conn, env, "AUTH_REJECTED", "no active task-scoped object grant")
            return
        try:
            dsm = self.runtime.bind(grant["owner"])
            operation, object_id = request.operation, request.object_id
            if operation == "read":
                if object_id not in grant["inputs"]:
                    raise ValueError("task may read only its assigned inputs")
                data = await dsm.read(object_id)
            elif operation == "alloc":
                if request.size <= 0 or request.size > 64 * 1024 * 1024:
                    raise ValueError("invalid task output size")
                object_id = await dsm.alloc(int(request.size))
                grant["outputs"].add(object_id)
                data = b""
            elif operation == "write":
                if object_id not in grant["outputs"]:
                    raise ValueError("task may write only its own output objects")
                await dsm.write(object_id, request.data)
                data = b""
            elif operation == "seal":
                if object_id not in grant["outputs"]:
                    raise ValueError("task may seal only its own output objects")
                await dsm.seal(object_id)
                data = b""
            elif operation == "free":
                if object_id not in grant["outputs"]:
                    raise ValueError("task may free only its own output objects")
                await dsm.free(object_id)
                grant["outputs"].discard(object_id)
                data = b""
            else:
                raise ValueError("unsupported task-scoped DSM operation")
            response = dsm_pb2.Envelope()
            response.compute_object_response.request_id = request.request_id
            response.compute_object_response.object_id = object_id
            response.compute_object_response.data = data
            if operation in ("alloc", "write"):
                record = self.runtime.object_manager.directory.get(object_id)
                if record is not None:
                    response.compute_object_response.storage_node_id = record.node_id
            await conn.send(response)
        except Exception as exc:
            code = error_to_code(exc) if isinstance(exc, DSMError) else "INVALID_REQUEST"
            await self._send_error(conn, env, code, str(exc))

    async def _cleanup_compute_outputs(self, task_id: str):
        grant = self._compute_grants.get(task_id)
        if grant is None:
            return
        dsm = self.runtime.bind(grant["owner"])
        for object_id in tuple(grant["outputs"]):
            try:
                await dsm.free(object_id)
            except Exception:
                pass

    async def _compute_status(self, env, conn):
        request = env.compute_status
        entry = self._compute_tasks.get(request.task_id)
        if entry is None or entry["owner"] != env.identity:
            await self._send_error(conn, env, "OBJECT_NOT_FOUND", "unknown compute task")
            return
        await self._send_compute_response(conn, request.request_id, request.task_id,
                                          entry["status"], entry["result"], entry["node_id"])

    async def _compute_cancel(self, env, conn):
        request = env.compute_cancel
        entry = self._compute_tasks.get(request.task_id)
        if entry is None or entry["owner"] != env.identity:
            await self._send_error(conn, env, "OBJECT_NOT_FOUND", "unknown compute task")
            return
        cancelled = entry["status"] is TaskStatus.QUEUED and entry["future"].cancel()
        if cancelled:
            entry["status"] = TaskStatus.CANCELLED
        await self._send_compute_response(conn, request.request_id, request.task_id,
                                          entry["status"], entry["result"], entry["node_id"])

    async def _send_compute_response(self, conn, rid, task_id, status, result=None, node_id=""):
        response = dsm_pb2.Envelope()
        data = response.compute_response
        data.request_id = rid
        data.task_id = task_id
        data.status = status.value
        if result is not None:
            data.node_id = result.node_id
            data.output_object_ids.extend(result.output_object_ids)
            data.output_metadata_json = json.dumps(result.output_metadata, allow_nan=False)
            data.metrics.update(result.metrics)
            data.error = result.error or ""
            data.operation = result.operation
            data.worker_hostname = result.worker_hostname
            data.worker_pid = result.worker_pid
            data.execution_started_at = result.execution_started_at
            data.execution_finished_at = result.execution_finished_at
        else:
            data.node_id = node_id
            data.output_metadata_json = "{}"
        await conn.send(response)

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
