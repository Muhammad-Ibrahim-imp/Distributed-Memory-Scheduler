import pytest
from common.types.errors import AuthenticationError, TokenExpiredError, TokenInvalidError
from common.types.security import TrustState
from security.authentication.registration import Role
from security.errors import IdentityBlockedError, SessionRevokedError
from security.monitoring.audit import AuditLogger

pytestmark = pytest.mark.security


def events(core):
    core.audit.flush()
    return list(AuditLogger.read_events(core.audit.path))


def types(core):
    return [e["event_type"] for e in events(core)]


def test_register_returns_credential_once_and_stores_only_hash(core):
    r = core.auth.register(Role.NODE, "node1")
    ident = core.identities.get(r.identity)
    assert ident.label == "node1" and ident.role is Role.NODE
    assert r.credential not in vars(ident).values() and ident.credential_hash != r.credential
    assert "node_registered" in types(core)


def test_client_registration_event(core):
    core.auth.register("client", "adapter")
    assert "client_registered" in types(core)


def test_two_rapid_registrations_never_collide(core):
    ids = {core.auth.register(Role.NODE, "same-label").identity for _ in range(200)}
    assert len(ids) == 200


def test_authenticate_and_validate(core):
    r = core.auth.register(Role.CLIENT, "c1")
    a = core.auth.authenticate(r.identity, r.credential, request_id="req-1")
    claims = core.auth.validate(a.token, claimed_identity=r.identity)
    assert claims.identity == r.identity and claims.role == "client"
    evs = events(core)
    assert any(e["event_type"] == "auth_success" and e["request_id"] == "req-1" for e in evs)
    assert any(e["event_type"] == "token_issued" for e in evs)


def test_wrong_credential_and_unknown_identity_look_identical(core):
    r = core.auth.register(Role.CLIENT, "c1")
    with pytest.raises(AuthenticationError) as e1:
        core.auth.authenticate(r.identity, "wrong")
    with pytest.raises(AuthenticationError) as e2:
        core.auth.authenticate("no-such-id", "wrong")
    assert str(e1.value) == str(e2.value)
    assert types(core).count("auth_failure") == 2


def test_expired_token_rejected_and_audited(core, clock):
    r = core.auth.register(Role.CLIENT, "c1")
    a = core.auth.authenticate(r.identity, r.credential)
    clock.advance(301)
    with pytest.raises(TokenExpiredError):
        core.auth.validate(a.token)
    assert "token_expired" in types(core)


def test_refresh_is_reauthentication_with_credential(core, clock):
    r = core.auth.register(Role.CLIENT, "c1")
    core.auth.authenticate(r.identity, r.credential)
    clock.advance(301)
    a2 = core.auth.authenticate(r.identity, r.credential)
    assert core.auth.validate(a2.token).identity == r.identity


def test_tampered_token_audited_as_failure(core):
    r = core.auth.register(Role.CLIENT, "c1")
    a = core.auth.authenticate(r.identity, r.credential)
    with pytest.raises(TokenInvalidError):
        core.auth.validate(a.token[:-3] + "AAA", claimed_identity=r.identity)
    assert "auth_failure" in types(core)


def test_token_bound_to_claimed_identity(core):
    a = core.auth.register(Role.CLIENT, "a"); b = core.auth.register(Role.CLIENT, "b")
    ta = core.auth.authenticate(a.identity, a.credential)
    with pytest.raises(AuthenticationError):
        core.auth.validate(ta.token, claimed_identity=b.identity)


def test_logout_revokes_session(core):
    r = core.auth.register(Role.CLIENT, "c1")
    a = core.auth.authenticate(r.identity, r.credential)
    core.auth.logout(a.token)
    with pytest.raises(SessionRevokedError):
        core.auth.validate(a.token)
    assert "session_revoked" in types(core)


def test_revoke_identity_kills_sessions_and_blocks_reauth(core):
    r = core.auth.register(Role.NODE, "n1")
    a = core.auth.authenticate(r.identity, r.credential)
    assert core.auth.revoke_identity(r.identity, "test") == 1
    with pytest.raises(IdentityBlockedError):
        core.auth.validate(a.token)
    with pytest.raises(IdentityBlockedError):
        core.auth.authenticate(r.identity, r.credential)


def test_only_revoked_trust_state_is_a_hard_stop(core, trust):
    r = core.auth.register(Role.NODE, "n1")
    for state in (TrustState.SUSPICIOUS, TrustState.QUARANTINED):   # policy §9.2 step 3
        trust.states[r.identity] = state
        core.auth.authenticate(r.identity, r.credential)
    trust.states[r.identity] = TrustState.REVOKED
    with pytest.raises(IdentityBlockedError):                       # correct credential, still refused
        core.auth.authenticate(r.identity, r.credential)
    evs = events(core)
    assert any(e["event_type"] == "authz_denied" and e["extra"]["reason"] == "trust_state_revoked" for e in evs)
    assert types(core).count("auth_failure") == 0                   # rejected before credential work


def test_revoked_state_blocks_even_a_valid_token(core, trust):
    r = core.auth.register(Role.NODE, "n1")
    a = core.auth.authenticate(r.identity, r.credential)
    trust.states[r.identity] = TrustState.REVOKED
    with pytest.raises(IdentityBlockedError):
        core.auth.validate(a.token, claimed_identity=r.identity)


def test_audit_log_never_contains_secrets(core):
    r = core.auth.register(Role.CLIENT, "c1")
    a = core.auth.authenticate(r.identity, r.credential)
    with pytest.raises(AuthenticationError):
        core.auth.authenticate(r.identity, "attempted-secret-guess")
    core.auth.validate(a.token)
    core.audit.flush()
    raw = core.audit.path.read_text()
    assert r.credential not in raw and a.token not in raw and "attempted-secret-guess" not in raw