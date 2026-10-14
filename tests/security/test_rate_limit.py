from network.security.rate_limit import RateLimiter


def test_20_pass_21st_rejected():
    lim = RateLimiter(clock=lambda: 1000.0)
    for _ in range(20):
        assert lim.check("id1") is True
    assert lim.check("id1") is False


def test_refill_over_time():
    now = [1000.0]
    lim = RateLimiter(clock=lambda: now[0])
    for _ in range(20):
        lim.check("id1")
    assert lim.check("id1") is False
    now[0] += 1.0   # +5 tokens
    assert lim.check("id1") is True