"""Authenticated remote DSM allocation/read/write/free smoke test."""

import argparse
import asyncio

from scripts.e2e_common import add_connection_options, clean, connect, run_cli


async def run(args):
    client = await connect(args)
    object_ids = []
    try:
        object_id = await asyncio.wait_for(client.alloc(64), timeout=15)
        object_ids.append(object_id)
        payload = b"authenticated DSM smoke test\x00\xff"
        await asyncio.wait_for(client.write(object_id, payload), timeout=10)
        result = await asyncio.wait_for(client.read(object_id), timeout=10)
        assert result == payload, f"payload mismatch: {result!r}"
        await asyncio.wait_for(client.free(object_id), timeout=10)
        object_ids.remove(object_id)
        print(f"DSM smoke test passed; identity={client.identity}, object={object_id}")
    finally:
        await clean(client, object_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_options(parser, "e2e-smoke")
    return run_cli(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
