import pytest
from pathlib import Path
from network.security.tls import client_context, server_context

CERTS = Path("certs")

pytestmark = pytest.mark.skipif(
    not (CERTS / "server.pem").exists(),
    reason="dev certs missing — run: python scripts/gen_certs.py",
)


def test_contexts_load():
    ctx_s = server_context()
    ctx_c = client_context()
    assert ctx_s.verify_mode.name == "CERT_REQUIRED"