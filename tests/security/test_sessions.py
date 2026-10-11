import pytest
from common.types.errors import TokenExpiredError, TokenInvalidError
from security.errors import SessionRevokedError
from security.sessions.manager import SessionManager

pytestmark = pytest.mark.security


def mgr(clock, mx=3):
    return SessionManager(ttl_seconds=300, max_per_identity=mx, clock=clock)


def test_create_validate(clock):
    m = mgr(clock)
    s = m.create("a", "client")
    assert m.validate(s.session_id, "a") is s


def test_bound_to_one_identity(clock):
    m = mgr(clock)
    s = m.create("a", "client")
    with pytest.raises(TokenInvalidError):
        m.validate(s.session_id, "someone-else")
    with pytest.raises(TokenInvalidError):
        m.validate("nope", "a")


def test_revoke_and_revoke_all(clock):
    m = mgr(clock)
    s1, _ = m.create("a", "node"), m.create("a", "node")
    assert m.revoke(s1.session_id) and not m.revoke(s1.session_id)
    with pytest.raises(SessionRevokedError):
        m.validate(s1.session_id, "a")
    assert m.revoke_all("a") == 1 and m.active_count("a") == 0


def test_expiry_and_sweep(clock):
    m = mgr(clock)
    s = m.create("a", "client")
    clock.advance(301)
    with pytest.raises(TokenExpiredError):
        m.validate(s.session_id, "a")
    assert m.sweep() == 1 and m.get(s.session_id) is None


def test_oldest_session_evicted_over_limit(clock):
    m = mgr(clock, mx=2)
    first = m.create("a", "client"); clock.advance(1)
    m.create("a", "client"); clock.advance(1)
    m.create("a", "client")
    assert m.active_count("a") == 2
    with pytest.raises(SessionRevokedError):
        m.validate(first.session_id, "a")