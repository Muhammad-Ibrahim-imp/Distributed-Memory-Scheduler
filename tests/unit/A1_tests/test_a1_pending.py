import asyncio
import pytest

from memory_node.allocator.allocator import Allocator
from memory_node.storage.storage import ObjectStore
from memory_node.metadata.metadata import MetadataStore
from memory_node.security.isolation import IsolationChecker
from memory_node.security.quotas import QuotaTracker
from memory_node.security.secure_delete import SecureDeleter
from memory_node.security.audit import SecurityAuditReporter
from memory_node.node import MemoryNode

node = MemoryNode("node-1", 1000)

CASES = [
    ("allocator.reserve", lambda: Allocator(1000).reserve(10)),
    ("allocator.release", lambda: Allocator(1000).release(10)),
    ("storage.put", lambda: ObjectStore().put("o", b"x")),
    ("storage.get", lambda: ObjectStore().get("o")),
    ("storage.delete", lambda: ObjectStore().delete("o")),
    ("metadata.add", lambda: MetadataStore().add(None)),
    ("metadata.get", lambda: MetadataStore().get("o")),
    ("metadata.remove", lambda: MetadataStore().remove("o")),
    ("isolation.check_access", lambda: IsolationChecker().check_access("a", "b")),
    ("quotas.check_within_quota", lambda: QuotaTracker().check_within_quota("a", 10)),
    ("secure_delete.wipe", lambda: SecureDeleter().wipe(bytearray(b"x"))),
    ("audit._send", lambda: SecurityAuditReporter("n").report_secure_delete_failed("o", "r")),
    ("node.alloc", lambda: asyncio.run(node.alloc(None, "o", "a", 10))),
    ("node.write", lambda: asyncio.run(node.write(None, "o", "a", b"x"))),
    ("node.read", lambda: asyncio.run(node.read(None, "o", "a"))),
    ("node.free", lambda: asyncio.run(node.free(None, "o", "a"))),
]


@pytest.mark.parametrize("name, call", CASES, ids=[c[0] for c in CASES])
def test_unimplemented_methods_raise(name, call):
    with pytest.raises(NotImplementedError):
        call()