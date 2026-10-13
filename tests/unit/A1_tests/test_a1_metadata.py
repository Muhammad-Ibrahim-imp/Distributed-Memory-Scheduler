from memory_node.metadata.metadata import MetadataStore, ObjectMetadata


def test_count_starts_at_zero():
    assert MetadataStore().count() == 0


def test_sha256_defaults_to_none():
    m = ObjectMetadata(object_id="o1", owner="alice", size=10, created_at=1.0)
    assert m.sha256 is None