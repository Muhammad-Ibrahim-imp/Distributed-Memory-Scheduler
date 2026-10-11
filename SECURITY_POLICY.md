# SECURITY_POLICY.md

**Owner:** M5 — Central Security
**Status:** Draft — Phase 1 (Week 1) deliverable
**Applies to:** A1 (Memory Node), A2 (DSM Abstraction), B1 (Network), B2 (Scheduler), M5 (Central Security)

---

## 0. How to read this document

This is the **policy**, not the implementation. M5 defines *what* the security rules are and *why*; A1, B1, and B2 each implement the *enforcement mechanism* for the pieces that belong to their layer, per spec §5/§13/§34 (Principle 3 — Distributed Security).

Every section follows the same structure: what this is and why, the concrete rule, who implements what (owner, deliverable, self-test), and any explicit 🔔 **Request to [Member]** — a literal ask M5 needs answered, binding unless a sync changes it.

**If anything here is ambiguous, underspecified, or doesn't cover a case you've hit: do not assume a resolution. Ask M5 before implementing.** A wrong assumption here is expensive precisely because four other people build against it.

---

## 1. Security Ownership Recap

```
A1 → Local memory/node security       (object isolation, quotas, secure deletion, SHA-256 integrity,
                                        local resource-abuse protection / node-level quotas)
B1 → Network/transport security        (TLS, request transport, replay ENFORCEMENT, connection-level
                                        rate-limit ENFORCEMENT)
B2 → Security-aware scheduling         (consumes trust state, excludes/restricts placement accordingly)
M5 → Central security policy/control   (identity, tokens, POLICY for replay/rate-limits, trust state
                                        machine, audit, authorization, this document)
```

M5 does not implement TLS, sockets, node scoring, or the DSM runtime. A1/B1/B2 do not define authentication, authorization, or the trust state machine — they enforce and consume what M5 defines here.

---

## 2. Identity Model

### 2.1 What this is and why

Before any other check can run, the system must answer: **who is making this request?** Two distinct populations exist:

- **Nodes** — memory nodes (A1's containers), join once, stay online, get scored for placement.
- **Clients** — application adapters (M5's own Image/Matrix adapters, and any future adapter), call `dsm_alloc/read/write/free`, never placed anywhere themselves.

### 2.2 The rule

Every node and client is issued, at registration, a record with three parts:

```json
{
  "identity": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "label": "node1",
  "role": "node",
  "credential_hash": "sha256(...)"
}
```

- **`identity`** is a UUID, generated locally at registration — never a sequential/human-chosen name. Sequential/self-asserted names create collision and reuse ambiguity across restarts and `docker-compose scale` operations.
- **`label`** is purely cosmetic, for logs/debugging — never used for security decisions.
- **`role`** determines downstream handling: a `node`'s misbehavior feeds the trust state machine (§6) and the security status format (§7.1) that B2 consumes; a `client`'s misbehavior is rejected directly at the authorization layer and never reaches B2.

### 2.3 Coordinator identity — explicit clarification

The coordinator container (running M5 + A2's DSM Runtime + B2's Scheduler, per §12) **never registers as a node itself** and is never a placement candidate. If the coordinator's physical machine also contributes RAM to the pool (a valid choice — see the "client machine also runs a node" decision), that RAM is contributed through a **separate, dedicated node container** that registers independently, exactly like any other node. The coordinator's own process/resources are never scheduled as DSM memory.

### 2.4 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | Registration endpoint, UUID/credential generation, credential hashing/storage | `security/authentication/registration.py` — `register(role, label) -> (identity, credential)` | Unit test: two rapid registrations never collide; credential is never stored/logged in plaintext |
| **A1** | On node startup, register once; persist `(identity, credential)` locally; include `identity` in every node-status report | Node boot performs registration before joining the pool | Verify restart behavior matches §2.5 request below |
| **B1** | Transmit `identity` + credential (registration) and `identity` + token (every subsequent request) in the request envelope | Envelope format includes `identity` | Unit test: a request missing `identity` is rejected before reaching business logic |
| **A2** | Ensure object metadata carries `owner_identity` | Object metadata schema includes `owner_identity` | Unit test: cross-client access on an owned object is rejected |

### 🔔 Request to A1
Confirm: should a node's `identity` persist across container restarts, or should every restart be a brand-new identity? **M5's default assumption unless told otherwise: fresh identity per container start.** Confirm or push back by Week 2.

### 🔔 Request to A2
Add `owner_identity: str` to object metadata, populated from the identity of the client that called `dsm_alloc`.

### 🔔 Request to B1
Confirm the exact field name/location for `identity` in your envelope/protocol definition.

---

## 3. Credential vs. Token

### 3.1 What this is and why

A **credential** is a long-lived secret proving "I am this identity," used **rarely** (registration, refresh-on-expiry). A **token** is a short-lived artifact proving "this identity was verified recently," used on **every request**. This split limits exposure of the long-term secret.

### 3.2 Credential — format and storage

- **Format:** `secrets.token_urlsafe(32)` — server-generated, high-entropy random string, not a human-chosen password.
- **Storage:** never store the raw credential. Store `sha256(credential)` only; verify with `hmac.compare_digest()` (constant-time).

### 3.3 Token — format and TTL

- **Format: Fernet** (Python `cryptography` library — already in the team's stack).
- **Payload:** `{"identity": "<uuid>", "role": "<role>", "session_id": "<uuid>", "issued_at": <timestamp>}`. `identity` is the only claim the pipeline acts on; `role` and `session_id` are carried so a session can be cancelled early — see §3.7.
- **TTL: 300 seconds.**
- **Expiry check:** `Fernet(key).decrypt_at_time(token, ttl=300, now)` — one call verifies signature, checks TTL, decrypts, in that order. No separately stored expiry. `decrypt_at_time` rather than `decrypt(token, ttl=...)` because it takes the clock explicitly, which is what makes TTL testable without sleeping.
- **Tamper vs. expiry:** `cryptography` raises the *same* `InvalidToken` for a failed HMAC and for an expired TTL. `verify_token` separates them by decrypting a second time *without* a TTL: if that also fails the token is forged (`TokenInvalidError`), and if it succeeds the token was genuine but old (`TokenExpiredError`, retryable). §6.6 and §9.4 both depend on that split.
- **Refresh:** on expiry, the client re-presents its credential to obtain a new token.

### 3.4 Why Fernet, not JWT

Fernet's payload is genuinely encrypted; a standard JWT's payload is only Base64-encoded and signed (readable by anyone). JWT's asymmetric-signing advantage solves a problem this system doesn't have — only M5's central security service ever verifies a token. Fernet gets TTL enforcement, integrity, and confidentiality from a library already in the stack, at no new-dependency cost.

### 3.5 Critical operational constraint

The Fernet key must be identical across every process that issues/verifies tokens — this is why central security runs as a single process (§12).

### 3.6 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | Key generation/storage, issue/verify functions | `security/authentication/tokens.py` — `issue_token(identity, role, session_id)`, `verify_token(token) -> TokenClaims` | Token verifies within TTL, fails after, fails if tampered |
| **A1/adapters** | Store issued token, attach to requests, re-auth on `InvalidToken` | Refresh logic | Expired-token request triggers transparent re-auth + retry |
| **B1** | Transmit token in envelope; distinguish `TOKEN_EXPIRED` from other rejections | Envelope carries `token`; distinct error codes | Expired vs. tampered distinguishable in error handling |

### 3.7 Sessions — why a token also carries `session_id`

A Fernet token is self-contained: it expires on its own and needs no server-side record to be *valid*. That is deliberate (§3.4), and it is also the limitation — a self-contained token cannot be cancelled before its TTL runs out. Logging out, or revoking one machine's access without revoking the whole identity, would otherwise be impossible.

So M5 keeps a small **session registry** (`security/sessions/manager.py`) mapping `session_id` → (identity, role, created, expires, revoked). It is **not** a token store — the token's own Fernet check is still what proves authenticity. The registry only ever *removes* validity:

- **No record, or a record for a different identity** → rejected as `TokenInvalidError`. Fail-closed: if the registry is lost, tokens stop working rather than working forever.
- **`revoke`** / **`logout`** kills one session; the identity's other sessions keep working.
- **`revoke_all`** kills every session for an identity — this is what `revoke_identity` (§7) calls when a trust state goes terminal.

Each kill logs `session_revoked`.

**Cost.** One dict lookup per validated request, in the same process (§12.1). **Benefit.** Revocation takes effect immediately instead of up to 300 seconds late — which matters because that window is exactly when a revoked node could still be reading.

**Known limitation (open item, §14).** The registry is in-memory, so a coordinator restart drops every session record and invalidates every outstanding token. With a 300-second TTL that is an available trade — clients simply re-authenticate — and it fails in the safe direction. If coordinator restart ever needs to be invisible to clients, the registry has to persist; that is the same open item as coordinator persistence.

### 🔔 Request to B1
Confirm your error schema can carry distinct codes: `TOKEN_EXPIRED` / `TOKEN_INVALID` / `AUTH_REJECTED`.

---

## 4. Nonce / Timestamp Window (Replay Protection)

### 4.1 What this is and why

A valid, correctly-authenticated request can still be **replayed** — captured and resent later, byte-for-byte identical. This is a distinct threat from forged identity: nothing is forged, a genuine request is repeated without authorization. This does not prevent interception (TLS/B1's job) — it guarantees that if a request is captured and resent, the underlying action executes **at most once**.

### 4.2 Ownership

Per the M5 plan's own wording ("you design this; B1 implements"), the split is:

```
M5 DEFINES:
    - nonce + timestamp required on every request
    - allowed timestamp window: 60 seconds
    - clock-skew tolerance: ±5 seconds
    - what counts as a replay, and the consequence (reject; feeds trust state)

B1 IMPLEMENTS:
    - nonce generation on the sending side (every outgoing request)
    - the nonce-tracking data structure (keyed by (identity, nonce))
    - timestamp/window/skew validation
    - replay detection and rejection
    - reporting a detected replay into M5's audit/trust-state functions (§10.4)
```

**Why this split, concretely:** B1 already owns the request envelope and the transport pipeline every request passes through — nonce generation and checking are naturally part of that pipeline, not a separate system M5 would otherwise have to hook into from outside. M5's role is defining the exact rule (window, skew, uniqueness scope) and consuming the *result* (a replay event) for audit logging and trust-state escalation — M5 does not maintain the tracking set itself.

### 4.3 The rule

- **Window: 60 seconds** from the server's current time.
- **Clock skew tolerance: ±5 seconds**, applied on top of the window.
- **Nonce uniqueness scope:** `(identity, nonce)` — a nonce reused by the same identity within the window is a replay.
- **Cleanup:** old nonces purged once their window has elapsed (implementation detail for B1 — e.g., a periodic sweep or an eviction-on-check strategy).

### 4.4 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | Defines window/skew/uniqueness-scope constants in `configs/security.yaml`; provides the audit/trust-state hooks B1 calls on a detected replay | Policy constants; `security/monitoring/audit.py` hook, `security/quarantine/trust_state.py` hook | Constants match this document |
| **B1** | Nonce generation, tracking structure, window/skew check, replay detection/rejection; call M5's hooks on detection | `network/security/replay.py` (or equivalent) — `validate_replay(identity, nonce, timestamp) -> bool` | Malicious-client fixture resending a captured request is rejected; same nonce accepted again after the window has genuinely elapsed |

### 🔔 Request to B1
Please confirm you can own the nonce-tracking data structure and replay-rejection logic as described above, and confirm the call signature you'll use to report a detected replay into M5's audit log and trust-state escalation (e.g., `security.report_event(identity, "replay_detected")`).

---

## 5. Rate Limits and Quota Policy

### 5.1 What this is and why

An identity can be fully authenticated and every request fully unique, and still be a problem — flooding or a buggy adapter hammering the system. This is a distinct failure mode from forged identity or replay.

### 5.2 Ownership — split across two layers

This isn't one mechanism — your own README lists two separate rate-limiting responsibilities at two different layers:

```
M5 DEFINES policy values for both layers below.

B1 IMPLEMENTS: connection-level rate limiting
    - throttles how fast a given identity can open connections /
      send requests to the coordinator over the wire
    - algorithm: token bucket, capacity 20, refill 5/sec (per identity)

A1 IMPLEMENTS: local resource-abuse protection / node-level quotas
    - throttles how much of A NODE'S OWN local resources a given
      client can consume (memory quota per client per node, object
      count limits, etc.)
    - exact numbers: OPEN — see request below
```

**Why token bucket for B1's layer specifically:** adapters legitimately burst — `ImageAdapter.prepare()` chunking a large image fires many `dsm_alloc`/`write` calls back-to-back, entirely legitimately. Token bucket allows a burst up to bucket size while still capping sustained abuse; a fixed/sliding window would reject the same legitimate burst it's trying to distinguish from an attack.

**Naming note:** to avoid confusion with the Fernet *session* token (§3), this document calls rate-limit units **"credits."**

### 5.3 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | Defines capacity/refill numbers for B1's layer; defines quota policy shape for A1's layer (exact numbers pending A1 input) | Policy constants in `configs/security.yaml` | Constants match this document |
| **B1** | Token-bucket connection-level limiter, per identity | `network/security/rate_limit.py` — `check_rate_limit(identity) -> bool`; distinct `RATE_LIMITED` error code | 20 rapid requests succeed, 21st fails; refills correctly over time |
| **A1** | Local per-client resource quota enforcement on each node | Quota check before accepting writes to local storage | A client exceeding its local quota on one node is rejected there, independent of global rate limiting |

### 🔔 Request to B1
Confirm `RATE_LIMITED` is a distinct, identifiable error code from auth/replay rejections (repeated violations feed trust-state escalation — M5 needs to tell these apart from your response).

### 🔔 Request to A1
Propose concrete numbers for local resource-abuse protection (e.g., max memory or max object count per client per node) — M5 doesn't have enough context on your allocator internals to propose sensible defaults here.

---

## 6. Trust State Machine

### 6.1 What this is and why

Everything above judges a single request in isolation. This tracks **node behavior over time**, feeding B2's placement decisions. Applies to **nodes only** — a misbehaving client is rejected directly by the checks above; there's no placement decision to inform for something that's never a storage target.

### 6.2 The states

```
TRUSTED      → eligible for placement
SUSPICIOUS   → restricted placement
QUARANTINED  → excluded from new placement
REVOKED      → excluded + active connections force-closed
```

### 6.3 Concrete behavior per state — proposed default, pending B2 confirmation

| State | Can connect? | Can read (existing data)? | Can write (existing objects)? | Can free? | Eligible for NEW placement? |
|---|---|---|---|---|---|
| **TRUSTED** | Yes | Yes | Yes | Yes | Yes |
| **SUSPICIOUS** | Yes | Yes | Yes | Yes | **No** (proposed default) |
| **QUARANTINED** | Yes | Yes (read-only, to allow safe retrieval) | **No** | **Yes** | No |
| **REVOKED** | **No** | No | No | No | No |

**Why `free` gets its own column.** A2 asked whether `free` counts as a mutation for this check, and it does — but it is the one mutation that *reduces* exposure rather than increasing it. Blocking it would strand a client's allocations and its quota on a quarantined node with no way to clean up, which is the same trap §7.4 exists to avoid. §6.3's "reads only" blocks content writes, not release. On REVOKED it is moot: the connection is already force-closed (§7.2), so the call fails at the transport layer before any of this is consulted.

**Reasoning behind this default:** SUSPICIOUS shouldn't cut off a node's existing, already-placed data — that would be disruptive for a state that's meant to be reversible and possibly caused by a transient issue. It should, however, stop the scheduler from making things worse by placing *new* data there until the node clears. QUARANTINED is more serious — new writes to existing objects are blocked too, but reads stay open specifically so data isn't stranded before a decision is made about it (see §7.4). REVOKED is the only state that actually severs the connection.

### 6.4 Transition rules

| Transition | Trigger | Automatic or Manual |
|---|---|---|
| TRUSTED → SUSPICIOUS | 3 failed authentications within 60s, OR 1 detected replay/tampered-request event, OR 1 rejected TLS client certificate, OR repeated rate-limit violations within a short window | Automatic |
| SUSPICIOUS → TRUSTED | 5 minutes with zero further violations | Automatic (checked lazily — §6.5) |
| SUSPICIOUS → QUARANTINED | Any further security violation while already SUSPICIOUS | Automatic |
| QUARANTINED → REVOKED | Any further violation while QUARANTINED | Automatic |
| QUARANTINED → TRUSTED | Explicit manual reinstatement only | **Manual — never automatic** |
| REVOKED → anything | None — terminal | Node must re-register as a brand-new identity |

### 6.5 How transitions are checked

- **Escalating transitions** are evaluated **event-driven, inline**, the instant a triggering violation is detected.
- **Recovery** (SUSPICIOUS→TRUSTED) is evaluated **lazily, on read** — whenever a node's state is looked up, the lookup function checks whether the clean window has elapsed and updates state before returning it.

### 6.6 Event → action → trust-state — consolidated reference

| Security event | Immediate action | Trust-state consequence |
|---|---|---|
| Invalid credential | Reject request | Counts toward the "3 failed auths/60s" trigger |
| Expired token | Reject, prompt refresh | None (routine, not a violation) |
| Replay detected | Reject request | TRUSTED→SUSPICIOUS trigger (single event) |
| Tampered request (token integrity check failed) | Reject | TRUSTED→SUSPICIOUS trigger (single event). Detected inside M5's `verify_token`, **not** by an envelope payload hash — see §3.6 and §9.3 |
| Rate-limit violation | Reject/throttle | Repeated violations → TRUSTED→SUSPICIOUS trigger |
| Unauthorized object access | Reject | Logged; does not by itself escalate node trust state (this is an authorization event, not evidence the *node* is compromised — flag to M5 if you think this should also escalate) |
| Any violation while SUSPICIOUS | Reject + escalate | → QUARANTINED |
| Any violation while QUARANTINED | Reject + escalate | → REVOKED |
| Manual revocation decision | Terminate access, force-close connection | → REVOKED directly |
| TLS certificate rejected | Reject connection | TRUSTED→SUSPICIOUS trigger (single event) |
| TLS handshake failure (no valid cert presented) | Reject connection | Logged only — routine on a lossy network, does not escalate |
| Malformed envelope (bad protobuf, or missing identity/token/nonce/timestamp) | Reject request | Logged only — during integration this is far more likely to be our own version skew than an attack |
| Timestamp outside the replay window | Reject request | Logged only — usually clock skew. Escalating here would let a node with an un-synced clock quarantine itself. Only actual nonce reuse escalates |
| Connection force-closed (consequence of REVOKED) | Close connection | None — this is the *result* of a transition, not a cause. Emitted by M5, not B1 |
| Secure delete failed | Log at WARNING | None — an operational failure, not evidence the node turned hostile. But it means data that should be gone may still be readable, so it is logged loudly. Repeated failures on one node come back to M5 as a proposal, not an automatic escalation |

**Why only one of the five transport events escalates:** the network layer sees a lot that looks alarming but isn't. A dropped handshake, a garbled frame, and a skewed clock are the normal failure modes of a distributed system running on student laptops over wifi. A rejected *certificate* is different in kind — it means someone presented a credential they should not have had, which is the same signal as a bad password. Escalating on the routine three would make the trust state machine fire on the integration environment rather than on an attacker, which also makes the "unauthorized node" test in the report meaningless.

### 6.7 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | State machine, transition logic, `get_trust_state(identity)` | `security/quarantine/trust_state.py` | Each transition rule fires correctly; QUARANTINED never auto-recovers |
| **B2** | Call `get_trust_state(node_id)` at every scoring decision; respect §6.3's permission table | Scoring function reads and respects trust state on every decision | Quarantining a node mid-test stops new placements without a scheduler restart |

### 🔔 Request to B2
Confirm the §6.3 permission table matches how you'd actually implement placement restriction — specifically, is "not eligible for new placement" enough signal, or do you need a numeric restriction (e.g., a lowered score rather than a hard exclusion) for SUSPICIOUS specifically?

---

## 7. Revocation & Quarantine Procedures

### 7.1 Security Status Format

```json
{
  "node_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "trust_state": "TRUSTED",
  "last_event": "auth_success",
  "failure_count": 0,
  "timestamp": 1730000012.4
}
```

### 7.2 How state changes propagate

- **Routine — pull-based.** B2 calls `get_trust_state(node_id)` at scoring time.
- **REVOKED — push, immediate.** Central security calls B1's `force_close_connection(identity)` directly the moment REVOKED fires (see §12 for why this is an in-process call, not a cross-container one).
- **QUARANTINED — no forced disconnect.** Existing reads continue (§6.3); only new placement stops.

### 7.3 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | `transition()` — updates state, calls B1's disconnect hook on REVOKED only | `security/quarantine/revocation.py` | REVOKED triggers exactly one disconnect call; QUARANTINED does not |
| **B1** | Expose `force_close_connection(identity: str) -> None`, callable in-process from M5's code | Function in the coordinator's B1-owned module | Calling it severs an active connection; subsequent requests from that identity fail at the transport layer |
| **B2** | Query security status at every placement decision, never cache beyond one decision | Scoring always reads current state | A node quarantined between two scheduling cycles is excluded from the next cycle onward |

### 7.4 Open item — what happens to a revoked node's existing objects

**This is genuinely undefined and needs A2's input — flagging rather than guessing.** When Node-3 transitions to REVOKED, it may currently own DSM objects that other clients still reference. Options:

- Objects become permanently inaccessible (Directory marks them unavailable; no automatic recovery)
- Objects are migrated to another node before/during revocation (requires a replication or migration mechanism this Level-1 design doesn't otherwise have)
- Objects are deleted outright
- Ownership/location handling is treated as outside M5's scope entirely — M5 only reports the revocation event; A2's Directory/Object Manager decides what to do with orphaned objects

**M5's proposed default, pending A2's confirmation:** M5 reports the revocation event (identity + timestamp) to A2 via the audit/event mechanism; A2's Directory marks that node's objects as unavailable and decides recovery/deletion policy, since object lifecycle after node loss is Directory/Object-Manager territory (this also overlaps with Principle 7 — failure-path handling generally, not specifically a security decision).

### 🔔 Request to A2
Please confirm how you want to be notified of a REVOKED node (a direct function call from M5, or M5 writing to a shared status field you poll), and confirm which of the options above (or another) you intend to implement for that node's existing objects.

### 🔔 Request to B1
Confirm you can implement `force_close_connection(identity: str) -> None` as an in-process call from M5 (not over a network/container boundary — see §12).

---

## 8. Authorization

### 8.1 What this is and why

Authentication answers "who are you." Authorization answers "are you allowed to do *this specific thing*." Per spec: an object belongs to a client — only that client may read/write/free it.

### 8.2 The rule

- Every DSM object's metadata carries `owner_identity` (§2, request to A2).
- On every `dsm_read`/`dsm_write`/`dsm_free`, M5's check compares the calling identity to `owner_identity`; mismatch → reject.
- Model: simple ownership-based (no RBAC/group-sharing needed for a Level 1 project).

### 8.3 Ownership boundary — explicit

```
A2: Stores/maintains object ownership metadata; enforces the ownership
    check at the point a DSM operation actually executes.
M5: Defines the authorization policy (what "unauthorized" means, what
    happens when it's detected — reject + audit log).
B1: Provides the authenticated caller identity the check is run against.
```

### 8.4 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | Ownership-check policy definition; audit logging of denials | `security/authorization/acl.py` — policy function | Denial is logged with correct fields |
| **A2** | Store `owner_identity`; enforce the check at the operation point using the authenticated identity B1/M5 provide | Object Manager enforces ownership | Owner can read/write/free; a different identity cannot |

### 🔔 Request to A2
Confirm the interface M5 should call (or that you'll call directly, using the identity M5/B1 provide) to enforce this — e.g., `check_ownership(identity, object_id) -> bool`, called by whichever of us actually sits closest to the operation.

---

## 9. Canonical Security Request Pipeline

### 9.1 Why this exists

Individual mechanisms (auth, replay, rate-limit, authorization, trust state) are each defined above, but without **one explicit, agreed order**, two teams can each build a locally-reasonable mechanism that doesn't compose correctly when integrated. This section is that one canonical order — the exact order can still be revised by team agreement, but there must be exactly one, not one per implementer's assumption.

### 9.2 The pipeline

```
Incoming request
      │
      ▼
[1] B1 — transport/TLS validation (connection established, cert valid)
      │
      ▼
[2] B1 — envelope parsing (extract identity, token, nonce, timestamp)
      │
      ▼
[3] M5 — trust-state check: REVOKED → hard reject immediately, no
         further processing. QUARANTINED/SUSPICIOUS → proceed, flagged
         for restricted handling per §6.3.
      │
      ▼
[4] M5 — token verification (identity + token validity)
      │
      ▼
[5] B1 — nonce/replay validation (per M5's policy, §4)
      │
      ▼
[6] B1 (connection-level) + A1 (node-level, once the request reaches
         a specific node) — rate-limit / quota check (§5)
      │
      ▼
[7] A2 — authorization/ownership check (§8)
      │
      ▼
[8] A2 — DSM operation (alloc/read/write/free)
      │
      ▼
[9] A1 — local memory operation
```

**Why trust-state check comes early (step 3, before token/nonce checks):** a REVOKED identity should be rejected as cheaply as possible, before spending effort on cryptographic verification that doesn't matter for an identity that's already been cut off. TRUSTED/SUSPICIOUS/QUARANTINED all continue to full verification since only REVOKED is a hard stop at the transport level.

### 🔔 Request to A1, A2, B1, B2 (all)
Please confirm this ordering matches how you're building your part of the pipeline, or propose changes at the Week 2 sync. This needs to be agreed once, not discovered as a mismatch during Week 6 integration.

### 9.3 Pre-authentication messages — the pipeline's one exception

The pipeline above assumes the request carries a token. A brand-new node has none, so two message types must be accepted **before** step 3:

```
Register   first contact. Carries the enrollment secret. Returns (identity, credential).
Refresh    re-presents a credential to replace an expired token (§3.3).
```

Rules for both:

- **Only these two.** Every other message type requires a verified token.
- **Rate-limited by source IP**, not by identity. §5.2 keys its limiter on identity, which by definition does not exist yet, so IP is the only available key — the single place in the design where IP is a rate-limit key.
- **`Register` is where enrollment control lands.** Without a check at this point, anyone who can reach the coordinator can register — which is precisely how a REVOKED node comes back. §6.4 makes REVOKED terminal for the *identity*, and nothing currently stops the same machine minting a new one. The enrollment secret is what closes that (open item, §14).

**Why this is an exception rather than a hole:** both messages are inherently unauthenticated, so their only defences are the enrollment secret and the IP-keyed limiter. Neither grants access to DSM data — `Register` mints an identity, and `Refresh` re-mints a token only for a credential that is still checked against its stored hash (§3.2).

### 9.4 Tamper detection lives in verify_token, not in a payload hash

Worth stating explicitly because it is the obvious thing to build twice. Three mechanisms could appear to detect tampering:

| Mechanism | What it actually protects | Needed? |
|---|---|---|
| TLS (B1, §9.2 step 1) | Integrity **in flight**, per connection | Yes — already in the plan |
| Fernet HMAC inside the token (§3.3) | Integrity **of the token itself** | Yes — free, already there |
| SHA-256 of the payload in the envelope | Integrity of the payload **after TLS terminates** | **No** — see below |

A payload hash would only add protection this design can use if something modifies a request *after* TLS has been terminated — a terminating proxy, or a compromised hop. This deployment has neither: the coordinator terminates TLS itself, in-process (§12). Adding it would hash every payload on every request to re-measure what TLS already checked, on hardware that is a student laptop.

So `TAMPER_DETECTED` means **the token's Fernet integrity check failed**. B1 already sees this: `cryptography` raises `InvalidToken` for a bad HMAC and for an expired TTL alike, and `verify_token` (§3.6) is what separates them into `TokenInvalidError` and `TokenExpiredError`. B1 maps the first to the `TOKEN_INVALID` wire code and reports `TAMPER_DETECTED`; the second is `TOKEN_EXPIRED`, routine, and escalates nothing (§6.6).

If a TLS-terminating proxy ever enters the deployment, revisit this — that is the condition that would justify the payload hash.

---

## 10. Audit Logging

### 10.1 What gets logged

Every security-relevant decision point: authentication attempts (success **and** failure), authorization denials, session issuance/refresh/expiry **and revocation** (`session_revoked`), every trust-state transition (with trigger), rate-limit violations, nonce/replay detections, tampered-request detections, node registration events, quota breaches (`QuotaExceededError`), the transport-layer events B1 reports — `tls_handshake_failure`, `tls_certificate_rejected`, `envelope_malformed`, `timestamp_out_of_window`, `connection_force_closed` — and A1's node-local `secure_delete_failed`.

### 10.2 Entry format

**JSON Lines (JSONL)** — one JSON object per line, append-only.

```json
{"event_id": "evt-8a2f1c", "timestamp": 1730000012.4, "event_type": "auth_failure",
 "severity": "warning", "actor_id": "node-3", "result": "rejected", "request_id": "req-9f0..."}
```

| Field | Purpose |
|---|---|
| `event_id` | Unique per entry — referenced directly in `SECURITY_REPORT.md` |
| `timestamp` | Feeds trust-state windowed counting (§6) |
| `event_type` | Fixed category — enables programmatic test assertions |
| `severity` | Distinguishes routine rejections from standout events |
| `actor_id` | The identity this event concerns |
| `result` | success / failure / rejected |
| `request_id` | Must match the request ID used across the rest of the system (Week-7 observability pass) |

### 10.3 What must NEVER be logged — no exceptions

- Raw passwords or credentials
- Raw Fernet tokens (log `token_ref: sha256(token)[:8]` if correlation is needed)
- Raw request payload contents (log that a write happened and its size, never the data)

### 10.4 Who implements what

| Member | Builds | Deliverable | Self-test |
|---|---|---|---|
| **M5** | Audit logger, log rotation, the hooks B1/A1 call to report their own detected events | `security/monitoring/audit.py` — `log_security_event(event_type, actor_id, severity, result, request_id, **extra)`, which writes the entry **and** feeds trust-state escalation (§6) internally | No line ever contains a raw token/credential/payload |
| **A1** | Call M5's audit function for local security violations (e.g., integrity check failures) | Calls `log_security_event(...)` | Confirm `event_type` naming with M5 |
| **B1** | Call M5's audit function for replay detections (§4), rate-limit violations (§5), and transport-layer events (§10.1) | Calls `log_security_event(...)` for each | Confirm `event_type` naming with M5 |

**One call, not two.** `log_security_event` is the single entry point: it builds a `SecurityEvent`, appends the JSONL line, then evaluates `(event_type, actor_id)` against §6.4's transition table and escalates if it matches. Reporters never call an escalation function separately. The reason is the asymmetry of the failure modes — if logging and escalating were two calls, forgetting the second is a silent security hole while forgetting the first is just a missing log line, and only the hole shows up in an incident.

**`identity` is one value with three names.** It is `identity` in the registration record (§2.2) and the request envelope, `actor_id` in an audit event (§10.2), and `node_id` in the security status format (§7.1). These must always carry byte-identical strings: `actor_id` is what the escalation table keys on, so any divergence silently updates the wrong record — or none.

### 🔔 Request to A1 (B1 resolved)
B1's transport event types are now in §10.1. **A1 — still open:** propose the `event_type` string values you'd emit for local security violations you detect, so M5 can add them to the fixed category list in §10.1.

---

## 11. Configuration

```yaml
token:
  ttl_seconds: 300

replay_protection:
  window_seconds: 60
  clock_skew_tolerance_seconds: 5

rate_limit:
  connection_level:      # B1
    bucket_capacity: 20
    refill_per_second: 5
  node_level:             # A1 — numbers pending A1 input, see §5
    max_memory_per_client: null
    max_objects_per_client: null

trust_state:
  suspicious_trigger:
    failed_auths_per_window: 3
    window_seconds: 60
  recovery_clean_period_seconds: 300

deployment:
  mode: "docker_single_host"   # or "multi_machine"
  security_host: "coordinator"  # or a real LAN IP in multi_machine mode
  security_port: 8443
```

---

## 12. Architecture Notes

### 12.1 Coordinator container composition

Central Security (M5), DSM Runtime/Object Manager/Directory (A2), and Scheduler (B2) are bundled into one **coordinator** container, as separate internal Python packages calling each other directly (in-process function calls) — not over Docker's internal network. Only memory nodes and clients get their own separate containers, because only nodes have a genuine reason for isolated resource limits (`mem_limit` simulating an independent machine's RAM).

### 12.2 B1 is not a separate container from the coordinator

B1 owns the network/transport *code*, but that code runs **wherever a socket needs to be handled** — inside every node's own container (the client side of each connection to the coordinator) and inside the coordinator container itself (the server side, accepting inbound connections, including whatever process is bound to the port published via `ports: "8443:8443"` in docker-compose). There is no dedicated "B1 container." When M5 calls `force_close_connection(identity)`, that is an **ordinary in-process function call within the coordinator container** — B1's connection-handling module and M5's security module are two Python packages in the same running process, not two containers talking over a network.

### 12.3 Network addressing

Within one Docker host, services reach the coordinator via Compose's built-in service-name DNS (`security_host: "coordinator"`). Across separate physical machines, nodes are instead given the coordinator's real LAN IP, with the coordinator's port explicitly published. This switch is a config change only (§11), never a code change.

---

## 13. What Belongs to M5 (Summary)

M5 builds/owns:
- Registration, identity/credential issuance (§2, §3)
- Token issuance and verification (§3)
- **Policy** for nonce/replay window and rate-limit thresholds (§4, §5) — not the tracking/enforcement mechanism, which is B1's (and A1's, for node-level quotas)
- Trust state machine and all transitions (§6)
- Revocation trigger logic — calls B1's hook, does not implement the hook itself (§7)
- Ownership/authorization policy (§8) — A2 enforces at the operation point
- The canonical security pipeline definition (§9)
- Audit logging infrastructure and the hooks other members call into (§10)
- This document, and the schemas it defines

M5 does **not** build: TLS/sockets/nonce-tracking/rate-limit enforcement (B1), node-level quotas (A1), node scoring/placement (B2), the DSM runtime/Object Manager/Directory/object-lifecycle-after-revocation (A2), or local node storage/allocation (A1).

---

## 14. Consolidated Requests — Quick Reference

| To | Request |
|---|---|
| A1 | Confirm identity persistence across restarts |
| A1 | Propose local resource-abuse-protection numbers (node-level quota) |
| A1 | Propose `event_type` values for locally-detected security violations |
| A2 | Add `owner_identity` to object metadata |
| A2 | Confirm ownership-check interface/call |
| A2 | Confirm revoked-node notification method and object-handling policy (§7.4) |
| B1 | Confirm envelope field name/location for `identity` |
| B1 | Confirm distinct error codes: `TOKEN_EXPIRED` / `TOKEN_INVALID` / `AUTH_REJECTED` / `RATE_LIMITED` |
| B1 | Confirm ownership of nonce-tracking/replay enforcement (§4) and the reporting call signature |
| B1 | Confirm `force_close_connection(identity)` as an in-process call (§7, §12) |
| B1 | Propose `event_type` values for transport-layer security events |
| B2 | Confirm §6.3 permission table and whether SUSPICIOUS needs a score penalty vs. hard exclusion |
| All | Confirm the canonical pipeline order (§9) |
| All | **Open:** agree the enrollment secret that gates pre-auth `Register` (§9.3) — where it is stored, how it is rotated, and how a node receives it |
| All | **Open:** whether the session registry must persist across coordinator restarts, or a 300 s re-authentication after restart is acceptable (§3.7) |

---

## 15. Testing Levels This Document Feeds

Per spec §21 and §32, M5 owns Integration Tests, Security Tests, and E2E Tests. Every rule above should have a corresponding pytest case, and the Week-8 security sweep (11 scenarios) should map directly onto sections 3–8 — each attack scenario exercises one specific rule defined here.

---

**Reminder:** anything ambiguous, anything not covered, any case that comes up during implementation that this document doesn't clearly answer — raise it with M5 before building around a guess.
