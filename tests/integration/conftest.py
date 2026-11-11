import pytest

from dsm.directory.directory import Directory
from dsm.object_manager import ObjectManagerConfig
from dsm.runtime import Runtime
from tests.mocks.fakes import FakeNodeClient, FakeScheduler, FakeSecurity


def _integration_runtime():
    directory = Directory()
    node = FakeNodeClient(capacity_bytes=64 * 1024 * 1024)
    scheduler = FakeScheduler(node_ids=["node-1"] * 1024)
    security = FakeSecurity()
    runtime = Runtime(directory, node, scheduler, security, ObjectManagerConfig())
    return runtime, node, security


@pytest.fixture
def integration_components():
    return _integration_runtime()
