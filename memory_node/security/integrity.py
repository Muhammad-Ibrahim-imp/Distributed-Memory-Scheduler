import hashlib

def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def verify_integrity(data: bytes, expected_hash: str) -> bool:
    return compute_sha256(data) == expected_hash