import pytest

from network.security.rate_limit import RateLimiter
from network.security.replay import ReplayGuard
from security.authentication.registration import Role


class MaliciousClient:
    def __init__(self, core, clock):
        registration = core.auth.register(Role.CLIENT, "malicious-client", "malicious-register")
        self.core = core
        self.clock = clock
        self.identity = registration.identity
        self.credential = registration.credential
        policy = core.config.rate_limit.connection_level
        self.rate_limiter = RateLimiter(policy.bucket_capacity, policy.refill_per_second, clock)
        replay_policy = core.config.replay_protection
        self.replay_guard = ReplayGuard(
            replay_policy.window_seconds,
            replay_policy.clock_skew_tolerance_seconds,
            clock,
        )

    def bad_credentials(self):
        return self.core.auth.authenticate(self.identity, "invalid-credential", "malicious-auth")

    def expired_token(self):
        session = self.core.auth.authenticate(self.identity, self.credential)
        self.clock.advance(self.core.config.token.ttl_seconds + 1)
        return self.core.auth.validate(session.token, claimed_identity=self.identity)

    def replay_request(self):
        timestamp = self.clock()
        first = self.replay_guard.check(self.identity, "reused-nonce", timestamp)
        second = self.replay_guard.check(self.identity, "reused-nonce", timestamp)
        return first, second

    def flood(self):
        accepted = 0
        for _ in range(self.rate_limiter.capacity + 1):
            if self.rate_limiter.check(self.identity):
                accepted += 1
        return accepted


@pytest.fixture
def malicious_client(core, clock):
    return MaliciousClient(core, clock)
