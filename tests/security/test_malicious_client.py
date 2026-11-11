import pytest

from common.types.errors import AuthenticationError, TokenExpiredError, TokenInvalidError
from security.monitoring.audit import AuditLogger


pytestmark = pytest.mark.security


def event_types(core):
    core.audit.flush()
    return [event["event_type"] for event in AuditLogger.read_events(core.audit.path)]


def test_bad_credentials_are_rejected_and_audited(core, malicious_client):
    with pytest.raises(AuthenticationError):
        malicious_client.bad_credentials()
    assert "auth_failure" in event_types(core)


def test_expired_session_token_is_rejected_and_audited(core, malicious_client):
    with pytest.raises(TokenExpiredError):
        malicious_client.expired_token()
    assert "token_expired" in event_types(core)


def test_replayed_nonce_is_rejected(malicious_client):
    first, second = malicious_client.replay_request()
    assert first.ok
    assert not second.ok and second.reason == "replay_detected"


def test_tampered_signed_token_is_rejected(core, malicious_client):
    session = core.auth.authenticate(malicious_client.identity, malicious_client.credential)
    token = session.token
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    with pytest.raises(TokenInvalidError):
        core.auth.validate(tampered, claimed_identity=malicious_client.identity)
    assert "auth_failure" in event_types(core)


def test_request_flood_exceeds_configured_bucket(malicious_client, core):
    accepted = malicious_client.flood()
    assert accepted == core.config.rate_limit.connection_level.bucket_capacity
