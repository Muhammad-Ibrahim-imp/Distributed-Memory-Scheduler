# Week 2 mini-milestone sync (M5)

Goal: confirm A1 / A2 / B1 / B2 read SECURITY_POLICY.md the same way M5 did. Ask each person to answer
in their own words, then compare with the answer in brackets.

## Everyone
1. Which check runs first on a request, and which trust state is a hard stop? (Trust-state check at §9.2 step 3; only REVOKED. SUSPICIOUS and QUARANTINED continue to full verification.)
2. What are the three names of the identity string? (`identity` / `actor_id` / `node_id`: byte-identical, §10.4.)
3. How do you report a security event? (One call: `security.monitoring.audit.log_security_event(event_type, actor_id, severity, result, request_id, **extra)`. It logs and feeds escalation. Never pass token, credential, password, secret, payload or data as extra keys: the event refuses them. Use `token_ref()` to correlate.)

## A1
- Numbers for `rate_limit.node_level` (max memory and max objects per client per node)?
- `event_type` values for node-local violations beyond `secure_delete_failed` and integrity failure?

## A2
- Confirm dependency direction: dsm imports security, never the reverse.
- Week 3: `check_access(identity, owner_identity, action, object_id)` in `acl.py`. Week 5: `register_revocation_listener(cb)` in `revocation.py`.

## B1
- Replay window 60 s, skew +-5 s, key `(identity, nonce)`: enforced exactly like this?
- Error codes distinct for TOKEN_EXPIRED / TOKEN_INVALID / AUTH_REJECTED. `SessionRevokedError` and `IdentityBlockedError` are new AuthenticationError subclasses, so they map to AUTH_REJECTED: OK?
- `force_close_connection(identity)` in-process signature.

## B2
- TRUSTED eligible; SUSPICIOUS, QUARANTINED, REVOKED excluded (you already hard-exclude). Is SUSPICIOUS "restricted" in policy §6.3 satisfied by exclusion?
- `TrustStateReader.get_trust_state(node_id)`: unknown ids return TRUSTED.

## Open items M5 is flagging
- ~~Token payload carries `session_id` and `role` in addition to policy §3.3's identity + issued_at. Policy text should be updated.~~ **Done** — §3.3 payload corrected and new §3.7 documents the session registry (including the restart trade-off).
- `EventType.SESSION_REVOKED` was added to `common/types/security.py`.
- `DSM_TOKEN_KEY` (Fernet key) is read from the environment and added to the coordinator service in docker-compose; empty means an ephemeral dev key.
- `register()` is an internal call until the Week-5 enrollment-secret gate exists.