"""Generate a dev CA + server cert + client cert for mTLS.
Server SAN covers 'coordinator' (Docker service name), 'localhost', 127.0.0.1.
Run from project root: python scripts/gen_certs.py
"""
import ipaddress
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT = Path("certs")
OUT.mkdir(exist_ok=True)
DAYS = 365


def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def save_key(k, path):
    path.write_bytes(k.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))


def save_cert(c, path):
    path.write_bytes(c.public_bytes(serialization.Encoding.PEM))


def main():
    ca_key = key()
    ca_subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dsm-dev-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subj).issuer_name(ca_subj)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    save_key(ca_key, OUT / "ca.key")
    save_cert(ca_cert, OUT / "ca.pem")

    srv_key = key()
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "coordinator")]))
        .issuer_name(ca_subj)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=DAYS))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("coordinator"),
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    save_key(srv_key, OUT / "server.key")
    save_cert(srv_cert, OUT / "server.pem")

    cli_key = key()
    cli_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dsm-client")]))
        .issuer_name(ca_subj)
        .public_key(cli_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=DAYS))
        .sign(ca_key, hashes.SHA256())
    )
    save_key(cli_key, OUT / "client.key")
    save_cert(cli_cert, OUT / "client.pem")
    print("certs written to certs/")


if __name__ == "__main__":
    main()