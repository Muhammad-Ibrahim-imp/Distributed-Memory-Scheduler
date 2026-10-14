import pytest
from network.security.tls import client_context, server_context

pytest.importorskip("ssl")


def test_contexts_load():
    pytest.importorskip("cryptography")
    ctx_s = server_context()
    ctx_c = client_context()
    assert ctx_s.verify_mode.name == "CERT_REQUIRED"