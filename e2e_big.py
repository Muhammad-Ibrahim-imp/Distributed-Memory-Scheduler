"""Authenticated DSM bounded-capacity and binary payload smoke test."""

import argparse
import asyncio

from scripts.e2e_common import add_connection_options, clean, connect, run_cli


async def run(args):
    client = await connect(args)
    object_ids = []
    try:
        for index in range(args.objects):
            object_id = await asyncio.wait_for(client.alloc(args.size), timeout=15)
            object_ids.append(object_id)
            payload = bytes((index + offset) % 256 for offset in range(args.size))
            await asyncio.wait_for(client.write(object_id, payload), timeout=15)
            assert await asyncio.wait_for(client.read(object_id), timeout=15) == payload
        print(f"Verified {len(object_ids)} payloads totaling {len(object_ids) * args.size} bytes")
    finally:
        await clean(client, object_ids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_options(parser, "e2e-bounded")
    parser.add_argument("--objects", type=int, default=6)
    parser.add_argument("--size", type=int, default=5000)
    args = parser.parse_args()
    if args.objects <= 0 or args.size <= 0:
        parser.error("--objects and --size must be positive")
    return run_cli(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
