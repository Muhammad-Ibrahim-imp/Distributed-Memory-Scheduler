import pytest
from common.types.errors import TokenExpiredError, TokenInvalidError
from security.authentication.tokens import TokenService

pytestmark = pytest.mark.security


def svc(clock, ttl=300):
    return TokenService(TokenService.generate_key(), ttl, clock)


def test_roundtrip(clock):
    s = svc(clock)
    c = s.verify_token(s.issue_token("id1", "node", "sess1"))
    assert (c.identity, c.role, c.session_id, c.issued_at) == ("id1", "node", "sess1", int(clock.now))


def test_expires_after_ttl(clock):
    s = svc(clock)
    t = s.issue_token("id1", "client", "s")
    clock.advance(299); s.verify_token(t)
    clock.advance(10)
    with pytest.raises(TokenExpiredError):
        s.verify_token(t)


def test_tampered_token_is_invalid_not_expired(clock):
    s = svc(clock)
    t = s.issue_token("id1", "client", "s")
    with pytest.raises(TokenInvalidError):
        s.verify_token(t[:-4] + ("AAAA" if not t.endswith("AAAA") else "BBBB"))


def test_wrong_key_and_garbage(clock):
    t = svc(clock).issue_token("id1", "client", "s")
    with pytest.raises(TokenInvalidError):
        svc(clock).verify_token(t)
    for junk in ["", "not-a-token", "é"]:
        with pytest.raises(TokenInvalidError):
            svc(clock).verify_token(junk)


def test_missing_key_gives_ephemeral_dev_key(clock):
    assert TokenService(None, 300, clock).ephemeral