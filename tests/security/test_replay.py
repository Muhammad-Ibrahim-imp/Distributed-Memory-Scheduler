from network.security.replay import ReplayGuard


def test_first_ok_second_replay():
    g = ReplayGuard(clock=lambda: 1000.0)
    assert g.check("id1", "n1", 1000.0).ok
    r = g.check("id1", "n1", 1000.0)
    assert not r.ok and r.reason == "replay_detected"


def test_different_identity_same_nonce_ok():
    g = ReplayGuard(clock=lambda: 1000.0)
    assert g.check("id1", "n1", 1000.0).ok
    assert g.check("id2", "n1", 1000.0).ok


def test_timestamp_out_of_window():
    g = ReplayGuard(clock=lambda: 1000.0)
    r = g.check("id1", "n1", 900.0)
    assert not r.ok and r.reason == "timestamp_out_of_window"


def test_nonce_cleared_after_window():
    now = [1000.0]
    g = ReplayGuard(clock=lambda: now[0])
    assert g.check("id1", "n1", 1000.0).ok
    now[0] = 1000.0 + 61 + 5 + 1
    assert g.check("id1", "n1", now[0]).ok