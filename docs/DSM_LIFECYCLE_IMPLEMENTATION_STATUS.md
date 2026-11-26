# DSM Lifecycle Implementation Status

Work is being delivered in independently tested milestones on `main`.

| Milestone | Status | Evidence / remaining work |
|---|---|---|
| 1. Authentication and credential recovery | Implemented; integration verified locally | Both real restart tests pass; see evidence below. Wrong-factor, lost-response retry, credential rotation and trust-state tests pass. Physical LAN testing remains outstanding. |
| 2. Connection ownership, heartbeat and session fencing | Pending | Handler task cleanup exists; explicit registry ownership, stale heartbeat fencing and timeout readiness are incomplete. |
| 3. A1 generation-tagged allocations and cleanup | Pending | Generation-tagged node storage, secure obsolete cleanup and accounting are not implemented. |
| 4. A2 incarnation, directory, cache and recovery | Pending | Context semantics and generation checks are implemented; incarnation-aware allocation recovery and all failure integration remain. |
| 5. B2 readiness and scheduler eligibility | Pending | Fresh generation-tagged heartbeat, cleanup completion and explicit readiness are incomplete. |
| 6. Integration, application regression and LAN operations | Pending | Full suite, all lifecycle scenarios, reusable startup scripts and LAN guide updates remain. |

## Milestone 1 evidence

- A returning node may use an explicit recovery registration only when the verified mTLS certificate maps to the stored identity and the configured enrollment factor is valid.
- A coordinator-generation change uses the previous generation only to select the recovery path; it is not an identity factor.
- Recovery rotates the credential hash, creates a fresh token and returns the active generation. Revoked identities and banned certificates remain rejected.
- Ordinary same-generation refresh continues to require the existing credential.
- `tests/integration/full_stack/test_daily_node_restart.py`: **1 passed**; local four-process shutdown/restart, three stable identities, preserved SQLite identity/audit history, and new coordinator generation.
- `tests/integration/full_stack/test_single_node_real.py`: **1 passed** after the credential-rotation exception fix; real coordinator restart and trusted/quarantined recovery, with revocation preserved.
- Focused security, persistence, enrollment, protocol and A2 tests: **103 passed** after adding wrong recovery-secret and lost-rotation-response retry assertions.
- Full suite: **520 passed, 1 skipped**, exit code 0, 140.10 seconds. The one skip is `test_integrity_failed_is_logged_locally_until_the_type_exists`; its recorded reason is “the shared event type now exists.” Transcript and JUnit report are under `.test-tmp/milestone1-full-final-20261126-115900/`.
- No physical four-laptop LAN test has been run.
