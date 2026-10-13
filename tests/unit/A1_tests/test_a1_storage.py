
from memory_node.storage.storage import ObjectStore


def test_store_starts_empty():
    assert ObjectStore()._data == {}