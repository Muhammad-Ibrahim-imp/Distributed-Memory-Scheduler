"""Generate dev mTLS certificates; issue one unique certificate per memory node."""
import argparse
import ipaddress
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT = Path("certs")
DAYS = 365


def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def save_key(value, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))


def save_cert(value, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.public_bytes(serialization.Encoding.PEM))


def issue_leaf(common_name, ca_cert, ca_key, san=None):
    leaf_key = key()
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=DAYS))
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                       critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
                       critical=False)
    )
    if san is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return leaf_key, builder.sign(ca_key, hashes.SHA256())


def issue_node(node_id, ca_cert, ca_key):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", node_id):
        raise ValueError("node id may contain only letters, digits, '_' and '-'")
    node_key, node_cert = issue_leaf(f"dsm-node-{node_id}", ca_cert, ca_key)
    save_key(node_key, OUT / "nodes" / f"{node_id}.key")
    save_cert(node_cert, OUT / "nodes" / f"{node_id}.pem")


def load_ca():
    ca_cert = x509.load_pem_x509_certificate((OUT / "ca.pem").read_bytes())
    ca_key = serialization.load_pem_private_key((OUT / "ca.key").read_bytes(), password=None)
    return ca_cert, ca_key


def initialize():
    OUT.mkdir(exist_ok=True)
    ca_key = key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dsm-dev-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    save_key(ca_key, OUT / "ca.key")
    save_cert(ca_cert, OUT / "ca.pem")

    server_key, server_cert = issue_leaf("coordinator", ca_cert, ca_key, [
        x509.DNSName("coordinator"), x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ])
    save_key(server_key, OUT / "server.key")
    save_cert(server_cert, OUT / "server.pem")

    client_key, client_cert = issue_leaf("dsm-client", ca_cert, ca_key)
    save_key(client_key, OUT / "client.key")
    save_cert(client_cert, OUT / "client.pem")
    issue_node("node-1", ca_cert, ca_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-node", metavar="NODE_ID",
                        help="issue another node certificate using the existing dev CA")
    args = parser.parse_args()
    if args.issue_node:
        issue_node(args.issue_node, *load_ca())
        print(f"issued certificate for {args.issue_node} in certs/nodes/")
    else:
        initialize()
        print("dev CA, server, client, and node-1 certificates written to certs/")


if __name__ == "__main__":
    main()
