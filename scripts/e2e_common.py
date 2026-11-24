"""Shared protected connection and cleanup helpers for the legacy E2E CLIs."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from network.tcp.dsmapi_stub import RemoteDSMAPI


def add_connection_options(parser: argparse.ArgumentParser, default_label: str) -> None:
    parser.add_argument("--host", default=os.getenv("DSM_COORDINATOR_HOST", "localhost"))
    parser.add_argument("--server-name", default=os.getenv("DSM_TLS_SERVER_NAME"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DSM_COORDINATOR_PORT", "8443")))
    parser.add_argument("--label", default=default_label)
    parser.add_argument("--ca-cert", default=os.getenv("DSM_CA_CERT_FILE"))
    parser.add_argument("--client-cert", default=os.getenv("DSM_CLIENT_CERT_FILE"))
    parser.add_argument("--client-key", default=os.getenv("DSM_CLIENT_KEY_FILE"))


async def connect(args: argparse.Namespace) -> RemoteDSMAPI:
    enrollment_secret = os.getenv("DSM_CLIENT_ENROLLMENT_SECRET")
    if not enrollment_secret:
        raise ValueError("set DSM_CLIENT_ENROLLMENT_SECRET in the process environment")
    for name, value in (("DSM_CA_CERT_FILE", args.ca_cert),
                        ("DSM_CLIENT_CERT_FILE", args.client_cert),
                        ("DSM_CLIENT_KEY_FILE", args.client_key)):
        if value:
            os.environ[name] = value
    client = RemoteDSMAPI(
        args.host, args.port, args.label, enrollment_secret,
        server_hostname=args.server_name or args.host,
    )
    try:
        await asyncio.wait_for(client.connect(), timeout=15)
    except BaseException:
        await client.close()
        raise
    return client


async def clean(client: RemoteDSMAPI, object_ids: list[str]) -> None:
    failures = []
    try:
        for object_id in reversed(object_ids):
            try:
                await asyncio.wait_for(client.free(object_id), timeout=10)
            except Exception as exc:
                failures.append((object_id, exc))
    finally:
        await asyncio.wait_for(client.close(), timeout=10)
    if failures:
        raise RuntimeError(f"failed to free {len(failures)} DSM object(s): {failures!r}")


def run_cli(coro) -> int:
    try:
        asyncio.run(coro)
        return 0
    except Exception as exc:
        print(f"E2E failed: {exc}", file=sys.stderr)
        return 1
