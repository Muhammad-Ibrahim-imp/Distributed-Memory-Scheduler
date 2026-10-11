import pytest

from common.types.security import TrustState
from security.authentication.tokens import TokenService
from security.bootstrap import build_security_core
from security.config import AuditPolicy, SecurityConfig
from tests.mocks.mock_dsm_api import MockDSMAPI


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTrust:
    """Stands in for M5's Week-5 TrustStateReader. Unknown ids are TRUSTED."""
    def __init__(self):
        self.states: dict[str, TrustState] = {}

    def get_trust_state(self, node_id: str) -> TrustState:
        return self.states.get(node_id, TrustState.TRUSTED)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def trust():
    return FakeTrust()


@pytest.fixture
def core(tmp_path, clock, trust):
    cfg = SecurityConfig(audit=AuditPolicy(path=str(tmp_path / "audit.jsonl")),
                         token_key=TokenService.generate_key())
    c = build_security_core(cfg, clock=clock, trust=trust)
    yield c
    c.audit.close()


@pytest.fixture
def dsm():
    return MockDSMAPI()