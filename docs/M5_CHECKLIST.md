# M5 integration checklist

Status reflects evidence from this feature branch on 2026-11-24.

| Area | Status | Evidence / remaining work |
|---|---|---|
| Durable node certificate binding | Implemented | SQLite schema v2; registration + binding transaction; unique fingerprint; reconnect match; revocation ban persistence. Security tests cover migration, duplicate binding, restart, quarantine, revocation, changed fingerprint, and database write failure. |
| Coordinator generation source | Implemented | One `CoordinatorGeneration` is created after security initialization and passed to both Runtime and CoordinatorServer. Real test checks startup generation against generation-qualified object IDs. |
| A2 Directory/object generation | Integrated from `origin/main` | Generation-qualified IDs, stale ID rejection, generation-scoped Directory/cache invalidation, failure and revocation state changes; A2 lifecycle tests passed. |
| Free in restricted trust state | Resolved | Quarantine can free; revoked returns `ObjectUnavailableError` and leaves metadata, per `SECURITY_POLICY.md` section 6.3. Regression tests cover both revoked callback and live-trust paths. |
| Real security path | Verified locally | Final suite passed with real TLS nodes and clients; invalid enrollment, duplicate and revoked certificate enrollment, ownership, replay, quota, quarantine read/write policy, scheduler exclusion, and persistent quarantine/revocation across restart. |
| Coordinator restart | Partially integrated | A2 invalidates old IDs/Directory/cache; security state persists. Safe old-allocation reclamation still requires A1 cleanup plus B1 fencing and B2 readiness. |
| Node failure / reconnect | Pending cross-component | A2 failure/cache handlers are wired to Registry's down callback. Current B1 transport does not unregister a node when its socket reader ends; A2 has no recovery callback to restore old objects, and current stats lack generation/readiness. |
| E2E tools | Implemented; syntax and CLI help passed | Protected environment secrets, host/port/server-name/cert options, bounded calls, payload checks, cleanup, exit codes. Multi-node tool requires successful-write evidence from at least two node logs. |
| LAN deployment guide | Prepared | Commands in `PHYSICAL_LAN_INTEGRATION.md`; no physical machine test has run. |
| Benchmark harness | Real smoke passed | `benchmark_remote.py` measured authenticated remote CRUD and asserted checksum/image/matrix correctness in the subprocess stack. JSON result output was checked. |
| Docker multi-node config | Static validation passed | Three services have distinct node labels, certificate paths, and key paths. `docker compose config -q` passed with temporary values. Daemon unavailable; no containers were started. |
| Extensibility | Verified | Generic `ChecksumAdapter` uses only `DSMAPI`; full-stack test exercises it through `RemoteDSMAPI`. |

## Teammate-owned integration gates

- **A1:** Store generation on each allocation; reclaim only explicitly obsolete generations after fencing; return idempotent cleanup results; report fresh capacity after cleanup. Preserve active-generation objects and do not reclaim on disconnect.
- **B1:** Add authenticated `coordinator_generation_id` to node registration/refresh, stats/readiness, and alloc/read/write/free request/response messages; reject stale requests before memory mutation; cancel/drain old-generation operations before cleanup; unregister confirmed disconnected nodes; reconnect with the same certificate-bound identity and generation readiness sequence.
- **B2:** Exclude a node until cleanup completion and a fresh active-generation heartbeat; keep quarantine/revocation exclusion independent of readiness; ensure resource statistics reflect the post-cleanup capacity.
- **A2:** Keep the new old-ID rejection and cache invalidation; add a recovery/readiness transition for a temporarily disconnected node only after B1 reports cleanup/readiness. Do not restore old IDs after a coordinator restart.

These gates block safe node reuse after coordinator restart and physical failure/reconnect acceptance. They do not block M5 certificate tests, independent adapters, local benchmark harness validation, or documentation.
