import pytest
from security.config import load_security_config


def test_loads_the_real_policy_file(monkeypatch):
    monkeypatch.setenv("DSM_TOKEN_KEY", "k")
    c = load_security_config("configs/security.yaml")
    assert c.token.ttl_seconds == 300
    assert (c.replay_protection.window_seconds, c.replay_protection.clock_skew_tolerance_seconds) == (60, 5)
    assert (c.rate_limit.connection_level.bucket_capacity, c.rate_limit.connection_level.refill_per_second) == (20, 5)
    assert c.trust_state.suspicious_trigger.failed_auths_per_window == 3
    assert c.trust_state.recovery_clean_period_seconds == 300
    assert c.enrollment.node_secret_env == "DSM_NODE_ENROLLMENT_SECRET"
    assert c.audit.path == "data/audit.jsonl" and c.token_key == "k"


def test_empty_env_key_means_ephemeral(monkeypatch):
    monkeypatch.setenv("DSM_TOKEN_KEY", "")
    assert load_security_config("configs/security.yaml").token_key is None


def test_unknown_keys_rejected(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("token:\n  ttl: 1\n")
    with pytest.raises(ValueError):
        load_security_config(p)
    p.write_text("bogus: {}\n")
    with pytest.raises(ValueError):
        load_security_config(p)