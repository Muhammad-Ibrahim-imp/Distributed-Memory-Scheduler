import ssl
import os
from pathlib import Path

def _cert_dir() -> Path:
    return Path(os.environ.get("DSM_CERT_DIR", "certs"))


def _certificate(env_name: str, default_name: str) -> Path:
    return Path(os.environ.get(env_name, _cert_dir() / default_name))


def server_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(
        _certificate("DSM_SERVER_CERT_FILE", "server.pem"),
        _certificate("DSM_SERVER_KEY_FILE", "server.key"),
    )
    ctx.load_verify_locations(_certificate("DSM_CA_CERT_FILE", "ca.pem"))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def client_context(certfile: str | Path | None = None,
                   keyfile: str | Path | None = None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_cert_chain(
        certfile or _certificate("DSM_CLIENT_CERT_FILE", "client.pem"),
        keyfile or _certificate("DSM_CLIENT_KEY_FILE", "client.key"),
    )
    ctx.load_verify_locations(_certificate("DSM_CA_CERT_FILE", "ca.pem"))
    ctx.check_hostname = True
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
