# DSM Level 1 Integration Report

**Run date:** 2026-11-24
**Starting commit:** `d098840200564623a0c6713305ee57a1e2558624`
**Working branch:** `feature/m5-full-integration`

## Verified in this environment

- The checkout was clean on `main` before work began. No remote branch was merged.
- Compared remote component branches with `origin/main`: A1, A2, B1, and M5 had no code diff; `B2/Scheduler` differs only in `docs/B2_IMPLEMENTATION_REPORT.md` (49 changed lines). This indicates those implementations are already represented in `main`; it does not independently verify their behavior.
- Baseline: Python 3.15.2; requirements installed from `requirements.txt`; `pytest -q --basetemp=.pytest-tmp-integration` completed with **493 passed, 1 skipped, 0 failed**. The skipped test is `tests/unit/A1_tests/test_a1_audit.py:32`, skipped because “the shared event type now exists.”
- The initial pytest run used the default Windows temp location and produced 71 setup errors because the sandbox denied access to `C:\Users\PMYLS\AppData\Local\Temp\pytest-of-PMYLS`. Using a workspace-local pytest base directory resolved that environment issue.
- Docker CLI version was available through the RTK wrapper, but the plain shell cannot resolve `docker`; Docker daemon and Compose execution were not verified. The local `certs/` directory has CA, client, and server materials, but no node certificates were present at inspection time.

## Integration status

The passing baseline is the existing repository suite, not a full-system proof. Existing integration tests under `tests/integration/` use fakes and mocks. No real coordinator, node processes, authenticated client workflow, multi-node placement, adapter-over-network run, failure injection, restart/recovery run, or benchmark was executed in this pass.

The source includes real coordinator and node entry points and a TLS `RemoteDSMAPI`, but the current E2E scripts instantiate clients with empty enrollment secrets, print success without asserting payload correctness, and do not guarantee cleanup on exceptions. `docker-compose.yml` defines only one fixed node (`node-1`). `RemoteDSMAPI` uses fixed TLS context defaults, re-registers on each `connect()`, and has no close/reconnect identity recovery workflow. These are concrete gaps to address before claiming the Level 1 definition of done.

No physical LAN systems were available. Do not interpret this report as evidence of three physical nodes, distributed computation, DSM object persistence, or physical-machine performance.

## Remaining work

The real local multi-node, adapter, ownership, invalid-token, replay, quota, expiry, and restart checks below are now implemented and executed. Remaining work is listed under “Remaining blockers and scope.”

## Continuation: real-stack implementation and execution

The following implementation was added after the baseline above:

- `tests/integration/full_stack/test_single_node_real.py` launches one coordinator and three independent `node_main.py` subprocesses, creates isolated TLS certificates and SQLite/audit paths, and connects real `RemoteDSMAPI` clients.
- The same workflow verifies TLS readiness, node registration and reconnect, allocation/write/read/free over the real request path, exact binary payload round trips, client ownership enforcement, invalid token rejection, replay rejection, quota enforcement, audit identity records, and cleanup.
- Invalid enrollment secrets are rejected, a captured 10-second session token expires and is rejected, and audit output is checked for enrollment denial and replay records.
- Deterministic placement allocates three 8 MiB objects against three 16 MiB nodes. The configured 50% per-client per-node quota forces separate nodes; the test checks each object ID in the receiving node's process log and asserts all three node logs contain one of the IDs.
- Image inversion, grayscale, and Gaussian blur run through the real DSM client with non-divisible 9x11 image dimensions and halo 2 for blur. Results are compared with OpenCV references. A non-divisible 5x3 by 3x4 matrix multiplication with block size 2 is compared with NumPy.
- The coordinator is restarted using the same temporary SQLite database. Nodes reconnect using their original identities and credentials; the existing client reconnects using its credential, its prior session token is rejected, its old DSM object ID is absent from the fresh in-memory Directory, and a new workload succeeds. Existing memory-node objects are not recovered or migrated.
- Minimal integration corrections: `node_main.py` now matches A1's `MemoryNode` keyword names; coordinator allocation replies use the protobuf's `allocate_response` field; coordinator/node config paths and TLS certificate directory accept test-specific environment paths; `RemoteDSMAPI` backs off and retries explicit rate-limit rejections with fresh nonce/timestamp; node socket closure triggers reconnect; and successful refresh reconnects the existing identity and restores a node in the live registry.

Executed command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/full_stack/test_single_node_real.py --basetemp=.pytest-tmp-fullstack -s
```

Result after the final security/restart assertions: **1 passed in 62.88 seconds**. This is a local four-process run at peak (coordinator plus three nodes), not Docker or physical LAN evidence.

Final regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp-final
```

Final result: **494 passed, 1 skipped in 74.44 seconds**. The skip remains `tests/unit/A1_tests/test_a1_audit.py:32`, whose condition says the shared event type now exists.

The first Docker investigation found that `docker` is not resolvable in the shell (`Get-Command docker` returned no executable). Compose validation and daemon availability therefore remain unverified; work continued using localhost processes as requested.

The B1-owned transport changes are limited to reconnect and rate-limit behavior in `network/tcp/node_service.py`, `network/tcp/coordinator.py`, and `network/tcp/dsmapi_stub.py`, plus dynamic cert directory lookup in `network/security/tls.py`. Review these contract changes with B1 before merging. The `node_main.py` constructor keyword correction bridges the entry point to A1's current API and should be reviewed with A1.

## Remaining blockers and scope

- The repository has no Docker executable on `PATH`; Compose syntax and daemon startup could not be checked. Owner: local environment. This blocks Docker validation only; localhost process integration passed.
- The coordinator restart test demonstrates that the in-memory Directory is lost and the old DSM object ID becomes unavailable while its bytes may remain allocated in a still-running node. There is no persistence/reconciliation contract. Owner: A2 architecture decision; this blocks crash-recovery guarantees only, not fresh workloads.
- Node certificate-to-identity bindings live in `EnrollmentGuard` memory and are not persisted. On restart, credential refresh rejects persisted banned certificates and revoked identities, but the original identity/certificate pairing is not available to compare. Owner: M5 with A1/B1 review; this blocks a strong persistent certificate-binding claim, not the demonstrated local reconnect.
- Quarantine/revocation scheduler exclusion through the real wire path, failed-node behavior, and restart trust-state restoration were not exercised by this full-stack test. Existing security persistence tests cover local persistence behavior; cross-component end-to-end evidence remains pending. Owner: M5/B1/B2.
- The legacy `e2e_test.py`, `e2e_multi.py`, and `e2e_big.py` scripts still need secure environment configuration and stronger assertions. Owner: M5; this does not block the pytest stack.
- Benchmark harnesses, Docker multi-node, and the physical LAN guide remain pending. The handoff and guide can be prepared independently; measured physical claims require team machines.
- Image/matrix tests show remote DSM storage and local adapter computation. They do not demonstrate remote computation or computational speedup.

Historical human/team actions from the first pass are superseded by this continuation.

## Continuation: A2 generation integration and M5-owned work (2026-11-24)

A2's commit `b74895d3ddbff1cd93a927d0ff0a53a2e1a61b7e` was fetched from `origin/main` and merged into `feature/m5-full-integration` without overwriting local edits. Its `Runtime`, `RequestContext`, `Directory`, Object Manager, NodeClient/API contracts, generation tests, and `docs/COORDINATOR_GENERATION.md` are integrated in commit `503f048` on the feature branch. The M5 handoff changes were subsequently merged into local `main`; see the merged-main verification and remote-publication status below.

M5 changes and integration hooks:

- Added `CoordinatorGeneration`, canonical lowercase UUID4, and passed one value to Runtime and CoordinatorServer. The server activates it before listening. The real test compares startup diagnostics with generation-qualified object IDs.
- Added durable SQLite v2 `node_certificates` bindings. Identity creation and verified fingerprint binding share one transaction; duplicate fingerprints/labels, changed certificates on refresh, and banned/revoked identities are denied. Schema v1 migration is covered. Private keys are never stored.
- Connected Runtime to the real trust-state reader and wired `NodeRegistry.on_node_down` to A2 failure/cache invalidation. B1 still needs to unregister confirmed disconnected identities and implement reconnect/readiness semantics.
- Applied `SECURITY_POLICY.md` section 6.3: quarantine allows read/free and blocks write; revocation denies read/write/free. A2's prior revoked-free path silently removed only local metadata, so it now raises `ObjectUnavailableError` and retains metadata for later safe cleanup.
- Modernized the three legacy E2E scripts; added a generic `ChecksumAdapter`; added a real `benchmark_remote.py`, three-node Compose configuration with unique node cert/key paths, static Compose checks, and LAN/benchmark handoff documents.

Executed evidence:

| Command / scenario | Result |
|---|---|
| A2 generation/object/cache/revocation tests + M5 enrollment/persistence tests | **89 passed** in the focused run; all were included in the final suite. |
| Real full-stack subprocess test with three nodes, TLS, placement, quarantine/revocation persistence, coordinator restart, checksum adapter and remote benchmark smoke | Passed as part of the final full suite. |
| Final full suite: `rtk proxy powershell -NoProfile -Command ".\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp-final-m5-release-20261124 -p no:cacheprovider"` | **512 passed, 1 skipped in 140.64 seconds**. The skip is the existing `tests/unit/A1_tests/test_a1_audit.py` shared-event skip. |
| `tests/unit/test_compose_config.py` | **1 passed**; YAML parses and node-1/2/3 identities, certificate paths, and key paths are unique. |
| `rtk proxy powershell -NoProfile -Command '$env:DSM_NODE_ENROLLMENT_SECRET="local-compose-check-node-secret"; $env:DSM_CLIENT_ENROLLMENT_SECRET="local-compose-check-client-secret"; $env:DSM_TOKEN_KEY="local-compose-check-token-key"; docker compose config -q'` | Passed static Compose validation. These temporary values were not written to `.env`. |
| `docker info --format '{{.ServerVersion}}'` | Docker CLI exists, but the Docker Desktop Linux engine named pipe is absent. No containers/volumes were started or modified. |
| `git diff --check` and `git diff --cached --check` | Passed at last check; rerun after final edits. |

The full-stack scenario verifies trust-state persistence and scheduler exclusion after restart, duplicate certificate enrollment denial, revoked-certificate re-enrollment denial, and quarantine read/write rules. Placement evidence uses successful write requests in node-side logs; allocation-attempt logs alone can include failed retries. The benchmark CLI ran over the real TLS API and checked remote payload correctness, the checksum adapter, all three image operations, and matrix output.

## Remaining cross-component gates

- **A1:** Tag each allocation with its generation; reclaim only obsolete generations after fencing; keep active-generation allocations; return idempotent cleanup results and post-cleanup capacity. Uncertain allocation cleanup and orphan reclamation remain unverified.
- **B1:** Carry authenticated `coordinator_generation_id` through node registration/refresh, heartbeat/readiness, and alloc/read/write/free requests and responses; reject stale requests before dispatch; drain/cancel old operations before cleanup; unregister on confirmed socket loss; reconnect the certificate-bound identity and report readiness. The current protobuf has no generation field, and `_accept` does not unregister when its reader ends.
- **B2:** Require cleanup completion plus a fresh active-generation resource heartbeat for placement; preserve separate trust-state exclusions and provide authoritative per-node usage export.
- **A2:** Generation IDs, Directory invalidation, old-ID rejection, cache clearing, and failure handling are present. Restore node object availability only after A1/B1/B2 cleanup/readiness; never restore old-generation IDs.
- Quarantine has no authenticated admin reinstatement endpoint. Unit recovery through `TrustStateMachine.reinstate()` exists; full-stack recovery awaits an approved administrative invocation path.
- Real disconnect-before/during/after-store and heartbeat-expiry cases await B1 registry-down handling and generation/readiness fields. Existing A2 failure/cache tests are component tests, not live-wire evidence.
- Physical LAN tests require a coordinator and separate node computers, DNS/firewall access, secure certificate provisioning, and operator secrets. They block final physical verification only.
- Docker daemon execution is blocked by the unavailable Docker Desktop engine; static Compose validation passed. Local subprocess integration is independent and passed.
- Benchmarks are end-to-end measurements; no physical performance or isolated cryptographic/audit overhead claim is made. Authoritative per-node stats and policy changes need A1/B2 operational support.

See `docs/COORDINATOR_RESTART_CONTRACT.md`, `docs/M5_CHECKLIST.md`, `docs/PHYSICAL_LAN_INTEGRATION.md`, and `docs/BENCHMARK_HANDOFF.md` for the shared interfaces, commands, owners, and acceptance gates.

## Merged-main verification (2026-11-24)

- Fetched current `origin/main` at `ce38420`, including A1's audit updates, and merged the feature branch without conflicts. Merge commit: `c74a8b46f8bf5398448f28da86dd88aa3305f64c` (`merge: integrate M5 full-system work`). A follow-up integration-test correction is commit `72f581104f0d063cf569b04193f8db7d0a9dc0bf`.
- Complete regression on the follow-up commit: `rtk proxy powershell -NoProfile -Command ".\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp-main-final-20261124 -p no:cacheprovider"` — **513 passed, 1 skipped in 145.35 seconds**.
- Explicit A1 audit test: `rtk proxy powershell -NoProfile -Command ".\.venv\Scripts\python.exe -m pytest -q -rs tests/unit/A1_tests/test_a1_audit.py --basetemp=.pytest-tmp-a1-audit-merged -p no:cacheprovider"` — **11 passed, 1 skipped**. `test_integrity_failed_event` now executes. The only skip is `test_integrity_failed_is_logged_locally_until_the_type_exists`, whose fallback is obsolete while the shared event type exists.
- Explicit real three-node subprocess workflow: `rtk proxy powershell -NoProfile -Command ".\.venv\Scripts\python.exe -m pytest -q -s tests/integration/full_stack/test_single_node_real.py --basetemp=.pytest-tmp-three-node-main-rerun-20261124 -p no:cacheprovider"` — **1 passed in 130.05 seconds**. It exercised three TLS-connected node processes, actual placement, security restrictions, restart, and adapter paths.
- Corrected an upstream stray `git` suffix in the A1 audit assertion. Stabilized the live quota assertion to request one byte beyond the per-node quota while the three nodes remain schedulable; the former 9 MiB request could fail earlier as out-of-capacity after nodes were quarantined.
- The merge was conflict-free. No conflict markers or manual conflict resolutions were needed in shared types, interfaces, security code, tests, or coordinator wiring.
- The final source/test tree passed `git diff --check`. The merge contained no certificates, private keys, enrollment credentials, temporary databases, logs, or generated test artifacts.

This establishes a common development baseline, not final integration acceptance. Generation-tagged storage reclamation (A1), authenticated transport generation propagation and fencing (B1), disconnected-node recovery (A2/B1), and cleanup/fresh-stat scheduler readiness (B2) remain incomplete. Full restart reclamation, Docker engine execution, and physical LAN deployment remain unverified.

The verified merge is committed locally. It has not been published to `origin/main`: Git Credential Manager could not obtain a GitHub credential (`SEC_E_NO_CREDENTIALS` / no username available), and the connected GitHub integration rejected the `main` ref update with `Resource not accessible by integration`. No force-push was attempted. The remote's last confirmed `main` commit was `ce38420475563001974f88abf76d9dbb91355e73`; publish the local `main` head after GitHub authentication or repository integration permissions are available.
