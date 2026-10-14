import ssl
from pathlib import Path

CERTS = Path("certs")


def server_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(CERTS / "server.pem", CERTS / "server.key")
    ctx.load_verify_locations(CERTS / "ca.pem")
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def client_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_cert_chain(CERTS / "client.pem", CERTS / "client.key")
    ctx.load_verify_locations(CERTS / "ca.pem")
    ctx.check_hostname = True
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx