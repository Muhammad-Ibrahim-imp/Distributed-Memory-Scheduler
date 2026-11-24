# Coordinator restart and generation contract

This is the shared Level 1 contract for coordinator restart fencing. Each runtime generation owns its Directory object IDs and every memory-node allocation created in that generation. Security identities, trust state, certificate bindings, bans, and audit history outlive a generation. Directory and object payload recovery are out of scope.

## Ownership

| Concern | Owner | Contract |
|---|---|---|
| Generation value and coordinator lifecycle hook | M5 | A canonical lowercase UUID4, created once after security/runtime initialization and before the listener accepts connections. Stable for the coordinator process lifetime. |
| Verified TLS fingerprint extraction and propagation | B1 | SHA-256 over `ssl_object.getpeercert(binary_form=True)`, lowercase 64-character hex. Never accept a fingerprint from a protobuf/client field. |
| Certificate binding and bans | M5 | SQLite binding from node identity to one immutable fingerprint; unique fingerprint; atomic identity+binding registration. Ban checks apply to enrollment and refresh. |
| Generation handshake, request fencing, connection lifecycle | B1 | Send the coordinator generation on node registration/refresh and every coordinator-to-node operation. Reject mismatched generations before dispatch. |
| Allocation tagging and secure reclaim | A1 | Every stored object carries its owning generation; reclaim only generations explicitly marked obsolete by the active coordinator after fencing. |
| Directory/cache validity | A2 | Object records and cache entries are generation-scoped. On a new generation, old IDs are invalid and stale cache entries cannot satisfy reads. |
| Readiness and scheduler eligibility | B2 | A node is excluded until cleanup for the active generation is complete and a resource heartbeat from that generation has arrived. Security state remains an independent exclusion filter. |

The coordinator entry point wires `NodeRegistry.on_node_down` to `Runtime.object_manager.handle_node_failure`, which marks associated Directory entries unavailable and evicts cached values. B1 must call `NodeRegistry.unregister(node_id)` on a confirmed transport loss, not on an ordinary request timeout; temporary disconnection retains node allocations. The current B1 accept path does not yet unregister identities when a socket reader ends, and reconnect does not restore A2 object availability after cleanup/readiness. Those are pending B1/A2 integration requirements.

## Identifiers and wire fields

The shared Python boundary is:

```python
@dataclass(frozen=True)
class RequestContext:
    identity: str
    request_id: str
    coordinator_generation: str | None = None

Runtime.start_generation(coordinator_generation: str) -> list[str]
```

Runtime-created contexts always have a non-null generation. The `None` default remains for older internal callers; A2 stamps the current generation before forwarding a node operation and rejects any explicit stale value. A2 returns `ObjectNotFoundError` for old-generation object IDs. A Runtime request whose explicit generation is stale raises `ObjectUnavailableError`.

Exact current A2-to-B1 signatures (all return `None` except `read`):

```python
async def alloc(ctx: RequestContext, node_id: str, object_id: str,
                owner_identity: str, size: int) -> None: ...
async def read(ctx: RequestContext, node_id: str, object_id: str,
               owner_identity: str) -> bytes: ...
async def write(ctx: RequestContext, node_id: str, object_id: str,
                owner_identity: str, data: bytes) -> None: ...
async def free(ctx: RequestContext, node_id: str, object_id: str,
               owner_identity: str) -> None: ...
```

The proposed B1 wire field is `coordinator_generation_id: string` on authenticated coordinator-to-node operations and responses. Registration/refresh binds the active generation before cleanup; readiness stats echo it. TLS fingerprints are derived only by the coordinator from `getpeercert(binary_form=True)` as lowercase SHA-256 hex, never from a client-supplied envelope field.

A2's implemented object IDs are `<coordinator_generation>.<uuid4>`; the generation is a canonical lowercase UUID4 and cannot contain `.`. `RequestContext.coordinator_generation` carries the active value from `Runtime` to every `NodeClient` call. `scripts/coordinator_main.py` creates one `CoordinatorGeneration` after security initialization and passes the same value to both `Runtime` and `CoordinatorServer`. The server activates the runtime with its generation before accepting connections, and its startup line logs that value.

`CoordinatorGeneration.value` is a string in canonical UUID4 form, for example `6f3c3cc0-c441-4edb-91b7-fc5b00b11114`. This value is process-local and is not persisted as the “current” value. A restarted coordinator always creates a different value. There is one active coordinator per shared node pool; multi-coordinator fencing requires a separate lease/consensus mechanism and is not supported.

The current protobuf `Envelope` has no generation field. B1 should add `coordinator_generation_id` as a string and carry it on node registration/refresh, heartbeat/readiness, and every alloc/read/write/free request. A1 must persist the value with each allocation. Client-facing DSM requests need not choose or supply a generation; the coordinator stamps its own current value when routing to a node. A response to a routed request must echo the generation so the coordinator can reject a late response from an old generation.

## Enrollment and certificate binding

The coordinator derives the fingerprint only from the verified peer certificate on its mTLS stream. M5 stores `identity -> fingerprint` in `node_certificates` with a uniqueness constraint on both columns. The registration transaction creates the node identity and binding atomically. Reusing a bound fingerprint for a new registration is denied; the existing identity must use credential refresh. Refresh must present the same verified certificate and a valid stored credential. Revoked identities and banned fingerprints remain denied after restart. Quarantine is retained and may reauthenticate, but it remains scheduler-ineligible until explicit recovery.

Bindings are immutable in Level 1. Certificate renewal/replacement is an administrative operation that currently has no API. Operators must create a new identity through an approved rotation procedure after retiring/revoking the old binding; do not edit the database or reuse the old identity with a new certificate. Private keys are never stored in SQLite.

## Generation lifecycle and fencing

1. The coordinator initializes SQLite, trust/auth services, scheduler, and TLS configuration, then creates one process generation and supplies it to Runtime and CoordinatorServer. Runtime/Server initialization must complete before the listener accepts connections; a failed startup cannot admit nodes.
2. Registration/refresh binds a node connection to the current generation. The coordinator sends cleanup-required/active-generation state to nodes before they are eligible for placement.
3. Before cleanup, B1 stops dispatching new requests for the old generation, cancels or drains in-flight operations, and closes old-generation channels. A1 must reject any request carrying a generation other than the node's active generation.
4. A1 securely reclaims only objects tagged with an explicitly obsolete generation and reports completion. A lost connection alone does not mark a generation obsolete and never triggers reclamation.
5. B1 reports cleanup completion and then forwards a fresh heartbeat tagged with the active generation. B2 excludes the node until both arrive. A2 rejects all object IDs whose generation differs from its current process generation and invalidates corresponding cache entries.
6. The coordinator admits new DSM traffic only after those component contracts report the node ready. Security trust restrictions continue to apply.

Proposed A1 boundary signatures (A1-owned implementation):

```python
async def activate_generation(generation_id: str) -> None: ...
async def cleanup_obsolete_generations(active_generation_id: str) -> CleanupReport: ...
```

`CleanupReport` must identify the active generation, completed obsolete generations, freed object count/bytes, and any failures. Cleanup is idempotent. It must never delete allocations carrying the active generation. B1 must not mark cleanup complete while an old-generation request can still mutate storage.

Proposed readiness report (B1 transport, B2 placement consumption):

```text
node_id, coordinator_generation_id, cleanup_complete, heartbeat_timestamp,
resource_snapshot, trust_state
```

The scheduler may select only nodes with `cleanup_complete == true`, matching current generation, a fresh resource snapshot, and a placement-eligible trust state. Existing security-policy behavior for QUARANTINED and REVOKED takes precedence.

## Errors and failure behavior

Per `SECURITY_POLICY.md` section 6.3, freeing a QUARANTINED node's object is allowed, but REVOKED denies read, write, and free. A2's prior local-metadata-only free path for revoked objects contradicted this table and could claim success while retaining orphaned bytes. The integrated behavior returns `ObjectUnavailableError` and retains Directory metadata; generation cleanup is responsible for eventual reclamation.

Use the existing protobuf error envelope with stable codes:

- `STALE_GENERATION`: request or response generation does not match the active generation; no storage mutation occurs.
- `NODE_NOT_READY`: node has not completed cleanup and supplied fresh active-generation statistics.
- `CLEANUP_INCOMPLETE`: obsolete-generation reclaim failed; node remains ineligible and healthy nodes continue serving.
- Existing `AUTH_REJECTED`/`TOKEN_INVALID`: invalid identity, credential, certificate binding, or revoked identity.
- Existing `OBJECT_NOT_FOUND`/`OBJECT_UNAVAILABLE`: old or failed-generation object IDs cannot be read.

Temporary TCP disconnection retains allocations and generation state at the node. A coordinator process restart changes the generation and invalidates all old DSM IDs. If fencing, cleanup, or readiness is uncertain, fail closed by excluding the node; do not infer cleanup from a timeout.

## Current implementation boundary

A2 generation-aware Directory/Object Manager changes from `origin/main` are integrated, including generation-qualified IDs, stale-ID rejection, Directory/cache invalidation, late read/write generation checks, failed-node state handling, and trust-state-aware operations. The remaining wire generation field, obsolete-request transport fencing, generation-tagged node storage cleanup, and node readiness are owned by B1, A1, and B2 respectively.

M5 provides `CoordinatorGeneration`, durable v2 SQLite certificate bindings, and enrollment/reconnect enforcement. A2's generation-aware Directory and Object Manager are integrated. The protobuf still has no generation field, and generation is not attached to node allocations. Generation-tagged reclaim is pending with A1, transport fencing with B1, and readiness/eligibility with B2. Until those pieces land, a restarted coordinator must not claim that old node allocations have been safely reclaimed or that nodes are safe for reuse under the finalized contract.

## Remaining implementation responsibilities

| Owner | Remaining work | Shared interface / files to change |
|---|---|---|
| **A1** | Tag every allocation with its coordinator generation; reject mismatched generations before touching storage; reclaim only allocations from generations explicitly made obsolete after B1 fencing; preserve active-generation allocations; make cleanup idempotent; report freed object/byte counts and post-cleanup capacity. A temporary disconnect must never trigger reclamation. | `memory_node/node.py`, `memory_node/metadata/metadata.py`, A1 allocation metadata and quota/capacity reporting; implement the proposed `activate_generation(generation_id: str)` and `cleanup_obsolete_generations(active_generation_id: str) -> CleanupReport` boundary. |
| **A2** | Preserve the implemented generation-qualified IDs, stale-ID rejection, Directory/cache invalidation, and failure handling. Add the recovery transition needed after a transiently disconnected node has reconnected and A1/B1/B2 confirm cleanup/readiness. Do not restore old-generation IDs or discard object metadata merely because a node is temporarily disconnected. | `dsm/directory/directory.py`, `dsm/object_manager/dsm_alloc.py`, `dsm/runtime/runtime.py`; integrate a readiness/recovery callback from B1/B2 while keeping `RequestContext.coordinator_generation` authoritative. |
| **B1** | Add authenticated generation propagation to node registration/refresh, operation requests/responses, and stats/readiness; reject stale requests before dispatch; fence, drain/cancel, and close old-generation work before cleanup; unregister a node on confirmed connection loss (not on an ordinary timeout); reconnect with its existing certificate-bound identity and report cleanup/readiness. | `common/protocol/dsm.proto` / generated `dsm_pb2.py`, `common/types/context.py`, `common/interfaces/NodeClient.py`, `network/tcp/coordinator.py`, `network/tcp/node_client.py`, `network/tcp/node_service.py`, `network/tcp/registry.py`. Add `coordinator_generation_id: string` to the authenticated node wire messages and echo it in operation responses. |
| **B2** | Require both A1 cleanup completion and a fresh resource heartbeat tagged with the active generation before placement; reject stale/missing stats; keep quarantine/revocation as independent placement exclusions; expose authoritative per-node capacity/utilization snapshots for the benchmark harness. | `common/interfaces/node_state.py`, `common/types/node.py`, `network/tcp/resource_state.py`, `scripts/coordinator_main.py` `DynamicNodeProvider`, `scheduler/*`. Consume `node_id, coordinator_generation_id, cleanup_complete, heartbeat_timestamp, resource_snapshot, trust_state`. |

The exact NodeClient and `RequestContext` signatures above are already present on this branch. The protobuf generation field, node allocation tag/cleanup report, and readiness fields are proposed shared wire/data contracts; they must be added by their owners before cross-process restart cleanup can be accepted. These changes do not add persistent Directory recovery, replication, or full crash recovery.

For A2 recovery integration, the proposed callback shape is `ObjectManager.handle_node_recovered(node_id: str, coordinator_generation: str) -> list[str]`. B1/B2 may invoke it only after the node has completed A1 cleanup and supplied fresh stats for that same generation. It restores only the still-current Directory records for the temporarily disconnected node; a new coordinator generation starts with an empty Directory, so no old-generation IDs are restored.
