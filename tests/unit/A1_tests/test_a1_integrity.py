from memory_node.security.integrity import compute_sha256, verify_integrity


def test_known_sha256_value():
    assert compute_sha256(b"hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_verify_accepts_matching_data():
    h = compute_sha256(b"hello")
    assert verify_integrity(b"hello", h) is True


def test_verify_rejects_tampered_data():
    h = compute_sha256(b"hello")
    assert verify_integrity(b"hello!", h) is False